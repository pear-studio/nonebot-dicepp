from __future__ import annotations

import json
import urllib.parse

import pytest

from dicepp_manager.docker_runtime import DockerRuntimeError
from dicepp_manager.docker_upgrade import DockerSocketUpgradeExecutor


BOT_OLD_ID = "sha256:" + ("1" * 64)
DASHBOARD_OLD_ID = "sha256:" + ("2" * 64)
BOT_NEW_ID = "sha256:" + ("3" * 64)
DASHBOARD_NEW_ID = "sha256:" + ("4" * 64)


def _inspect(role: str, *, image: str, running: bool = True):
    name = "dicepp" if role == "bot" else "dicepp-dashboard"
    service = role
    return {
        "Id": (role[0] * 64),
        "Image": BOT_OLD_ID if role == "bot" else DASHBOARD_OLD_ID,
        "Name": f"/{name}",
        "Config": {
            "Image": image,
            "Cmd": [f"run-{role}"],
            "Entrypoint": ["/entrypoint"],
            "Env": [f"ROLE={role}"],
            "WorkingDir": "/app",
            "Labels": {
                "com.docker.compose.project": "dicepp",
                "com.docker.compose.service": service,
                "io.dicepp.managed": "true" if role == "bot" else "false",
            },
        },
        "HostConfig": {
            "Binds": [f"/host/{role}:/app/{role}"],
            "NetworkMode": "dicepp_manager-net",
            "RestartPolicy": {"Name": "unless-stopped"},
        },
        "NetworkSettings": {
            "Networks": {
                "dicepp_manager-net": {
                    "Aliases": [name],
                    "IPAMConfig": None,
                }
            }
        },
        "State": {"Running": running},
    }


class Runtime:
    def __init__(self) -> None:
        self._allowed = {"dicepp-runtime"}
        self.requests = []
        self.current = {
            "dicepp": "a" * 64,
            "dicepp-dashboard": "d" * 64,
        }
        # container_id -> inspect payload returned verbatim
        self.extra_identities: dict[str, dict] = {}

    async def _resolve_container(self, unit):
        assert unit == "dicepp-runtime"
        return "a" * 64

    async def _request(
        self, method, path, *, expected, raw=False, json_body=None
    ):
        self.requests.append((method, path, json_body))
        if path.startswith("/containers/") and path.endswith("/json"):
            container_id = path.split("/", 2)[2].removesuffix("/json")
            if container_id in self.extra_identities:
                return self.extra_identities[container_id]
            # Docker inspect returns the requested id; keep it consistent with
            # the name-based container list so identity checks can match.
            if container_id == "a" * 64:
                payload = _inspect("bot", image="old-bot")
                payload["Id"] = container_id
                return payload
            if container_id == "d" * 64:
                payload = _inspect("dashboard", image="old-dashboard")
                payload["Id"] = container_id
                return payload
        if path.startswith("/images/") and path.endswith("/json"):
            encoded = path.removeprefix("/images/").removesuffix("/json")
            identity = urllib.parse.unquote(encoded)
            by_identity = {
                BOT_OLD_ID: ("bot", BOT_OLD_ID),
                DASHBOARD_OLD_ID: ("dashboard", DASHBOARD_OLD_ID),
                "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0": (
                    "bot",
                    BOT_NEW_ID,
                ),
                "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0": (
                    "dashboard",
                    DASHBOARD_NEW_ID,
                ),
            }
            role, image_id = by_identity[identity]
            return {
                "Id": image_id,
                "Config": {
                    "Cmd": [f"run-{role}"],
                    "Entrypoint": ["/entrypoint"],
                    "Env": [f"ROLE={role}"],
                    "WorkingDir": "/app",
                },
            }
        if path.startswith("/containers/json?"):
            filters = json.loads(
                urllib.parse.unquote(path.split("filters=", 1)[1])
            )
            if "label" in filters:
                return [{"Id": "d" * 64}]
            pattern = filters["name"][0]
            name = pattern.removeprefix("^/").removesuffix("$")
            current = self.current.get(name)
            return [{"Id": current}] if current else []
        if method == "DELETE":
            container_id = path.split("/", 2)[2].split("?", 1)[0]
            for name, current in list(self.current.items()):
                if current == container_id:
                    del self.current[name]
            return {}
        if method == "POST" and path.startswith("/containers/create?name="):
            name = urllib.parse.unquote(path.split("name=", 1)[1])
            container_id = ("b" if name == "dicepp" else "e") * 64
            self.current[name] = container_id
            return {"Id": container_id}
        return {}


@pytest.mark.asyncio
async def test_socket_upgrade_recreates_only_bot_dashboard_with_preserved_topology():
    runtime = Runtime()
    executor = DockerSocketUpgradeExecutor(runtime)
    targets = [
        {
            "role": "bot",
            "reference": "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
            "image_id": BOT_NEW_ID,
        },
        {
            "role": "dashboard",
            "reference": "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0",
            "image_id": DASHBOARD_NEW_ID,
        },
    ]

    previous = await executor.capture_images(targets)
    resolved = await executor.resolve_images(targets)
    result = await executor.switch_images(
        target_images=resolved,
        previous=previous,
    )

    assert result["status"] == "switched"
    creates = [
        body
        for method, path, body in runtime.requests
        if method == "POST" and path.startswith("/containers/create?")
    ]
    assert [body["Image"] for body in creates] == [
        BOT_NEW_ID,
        DASHBOARD_NEW_ID,
    ]
    assert all("Cmd" not in body for body in creates)
    assert all("Entrypoint" not in body for body in creates)
    assert all("Env" not in body for body in creates)
    assert creates[0]["HostConfig"]["Binds"] == ["/host/bot:/app/bot"]
    assert creates[1]["HostConfig"]["Binds"] == [
        "/host/dashboard:/app/dashboard"
    ]
    assert set(creates[0]["NetworkingConfig"]["EndpointsConfig"]) == {
        "dicepp_manager-net"
    }
    assert all("/images/create" not in path for _method, path, _body in runtime.requests)
    assert all("pull" not in path for _method, path, _body in runtime.requests)

    restored = await executor.restore_images(previous)

    assert restored["status"] == "restored"
    rollback_creates = [
        body
        for method, path, body in runtime.requests
        if method == "POST" and path.startswith("/containers/create?")
    ][-2:]
    assert [body["Image"] for body in rollback_creates] == [
        BOT_OLD_ID,
        DASHBOARD_OLD_ID,
    ]


@pytest.mark.asyncio
async def test_socket_upgrade_rejects_loaded_tag_with_wrong_immutable_id():
    runtime = Runtime()
    executor = DockerSocketUpgradeExecutor(runtime)
    records = [
        {
            "role": "bot",
            "reference": "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
            "image_id": "sha256:" + ("9" * 64),
        },
        {
            "role": "dashboard",
            "reference": "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0",
            "image_id": DASHBOARD_NEW_ID,
        },
    ]

    with pytest.raises(DockerRuntimeError, match="identity differs"):
        await executor.resolve_images(records)


@pytest.mark.asyncio
async def test_socket_upgrade_rejects_unhandled_nondefault_host_config():
    runtime = Runtime()
    original_request = runtime._request

    async def request(method, path, **kwargs):
        payload = await original_request(method, path, **kwargs)
        if path == f"/containers/{'a' * 64}/json":
            payload["HostConfig"]["DeviceRequests"] = [{"Driver": "nvidia"}]
        return payload

    runtime._request = request
    executor = DockerSocketUpgradeExecutor(runtime)
    records = [
        {
            "role": "bot",
            "reference": "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
            "image_id": BOT_NEW_ID,
        },
        {
            "role": "dashboard",
            "reference": "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0",
            "image_id": DASHBOARD_NEW_ID,
        },
    ]

    with pytest.raises(DockerRuntimeError, match="DeviceRequests"):
        await executor.capture_images(records)


@pytest.mark.asyncio
async def test_socket_upgrade_preserves_only_explicit_image_default_overrides():
    runtime = Runtime()
    original_request = runtime._request

    async def request(method, path, **kwargs):
        payload = await original_request(method, path, **kwargs)
        if path == f"/containers/{'a' * 64}/json":
            payload["Config"]["Cmd"] = ["custom-bot"]
            payload["Config"]["Env"] = ["ROLE=bot", "CUSTOM=yes"]
        return payload

    runtime._request = request
    executor = DockerSocketUpgradeExecutor(runtime)
    records = [
        {
            "role": "bot",
            "reference": "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
            "image_id": BOT_NEW_ID,
        },
        {
            "role": "dashboard",
            "reference": "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0",
            "image_id": DASHBOARD_NEW_ID,
        },
    ]

    previous = await executor.capture_images(records)
    resolved = await executor.resolve_images(records)
    await executor.switch_images(target_images=resolved, previous=previous)

    bot_create = next(
        body
        for method, path, body in runtime.requests
        if method == "POST"
        and path.startswith("/containers/create?name=dicepp")
        and "dashboard" not in path
    )
    assert bot_create["Cmd"] == ["custom-bot"]
    assert bot_create["Env"] == ["CUSTOM=yes"]


@pytest.mark.asyncio
async def test_socket_upgrade_rejects_ambiguous_removed_image_default():
    runtime = Runtime()
    original_request = runtime._request

    async def request(method, path, **kwargs):
        payload = await original_request(method, path, **kwargs)
        if path == f"/containers/{'a' * 64}/json":
            payload["Config"]["Env"] = []
        return payload

    runtime._request = request
    executor = DockerSocketUpgradeExecutor(runtime)
    records = [
        {
            "role": "bot",
            "reference": "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
            "image_id": BOT_NEW_ID,
        },
        {
            "role": "dashboard",
            "reference": "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0",
            "image_id": DASHBOARD_NEW_ID,
        },
    ]
    previous = await executor.capture_images(records)
    resolved = await executor.resolve_images(records)

    with pytest.raises(DockerRuntimeError, match="manual upgrade required"):
        await executor.switch_images(
            target_images=resolved,
            previous=previous,
        )

    assert all(
        not (method == "DELETE" or path.startswith("/containers/create?"))
        for method, path, _body in runtime.requests
    )


@pytest.mark.asyncio
async def test_socket_upgrade_rejects_changed_defaults_even_when_effective_equals_old():
    runtime = Runtime()
    original_request = runtime._request

    async def request(method, path, **kwargs):
        payload = await original_request(method, path, **kwargs)
        if path == (
            "/images/"
            + urllib.parse.quote(
                "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
                safe="",
            )
            + "/json"
        ):
            payload["Config"]["Cmd"] = ["new-default"]
            payload["Config"]["Env"] = ["ROLE=bot", "NEW_DEFAULT=yes"]
        return payload

    runtime._request = request
    executor = DockerSocketUpgradeExecutor(runtime)
    records = [
        {
            "role": "bot",
            "reference": "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
            "image_id": BOT_NEW_ID,
        },
        {
            "role": "dashboard",
            "reference": "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0",
            "image_id": DASHBOARD_NEW_ID,
        },
    ]
    previous = await executor.capture_images(records)
    resolved = await executor.resolve_images(records)

    with pytest.raises(
        DockerRuntimeError,
        match="provable Compose override source.*Cmd.*Env|"
        "provable Compose override source.*Env.*Cmd",
    ):
        await executor.switch_images(
            target_images=resolved,
            previous=previous,
        )

    assert all(
        not (method == "DELETE" or path.startswith("/containers/create?"))
        for method, path, _body in runtime.requests
    )


@pytest.mark.asyncio
async def test_socket_upgrade_rejects_replacing_unrelated_same_name_container():
    """A same-name container with an unknown identity is never deleted."""
    runtime = Runtime()
    executor = DockerSocketUpgradeExecutor(runtime)
    records = [
        {
            "role": "bot",
            "reference": "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
            "image_id": BOT_NEW_ID,
        },
        {
            "role": "dashboard",
            "reference": "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0",
            "image_id": DASHBOARD_NEW_ID,
        },
    ]
    previous = await executor.capture_images(records)
    resolved = await executor.resolve_images(records)
    # The captured bot container was replaced by an unrelated container that
    # merely shares the name.
    runtime.current["dicepp"] = "c" * 64

    with pytest.raises(DockerRuntimeError, match="identity"):
        await executor.switch_images(target_images=resolved, previous=previous)

    assert all(
        method != "DELETE" for method, _path, _body in runtime.requests
    )
    assert not any(
        method == "POST" and path.startswith("/containers/create?")
        for method, path, _body in runtime.requests
    )


@pytest.mark.asyncio
async def test_socket_upgrade_allows_replacing_transaction_labeled_container():
    """The same-name container carrying this transaction's label is authorized.

    This is the takeover retry path: the Updater already created the target
    runtime (new id, transaction label) and the coordinator replaces it again.
    """
    runtime = Runtime()
    executor = DockerSocketUpgradeExecutor(runtime)
    records = [
        {
            "role": "bot",
            "reference": "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
            "image_id": BOT_NEW_ID,
        },
        {
            "role": "dashboard",
            "reference": "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0",
            "image_id": DASHBOARD_NEW_ID,
        },
    ]
    previous = await executor.capture_images(records)
    resolved = await executor.resolve_images(records)
    transaction_id = "t" * 32
    labeled = _inspect("bot", image="old-bot")
    labeled["Config"]["Labels"]["io.dicepp.upgrade-transaction"] = transaction_id
    runtime.extra_identities["c" * 64] = labeled
    runtime.current["dicepp"] = "c" * 64

    await executor._replace(
        previous["containers"]["bot"],
        resolved["bot"],
        extra_labels={
            "io.dicepp.upgrade-transaction": transaction_id,
            "io.dicepp.upgrade-role": "runtime",
        },
        restart_policy="no",
        start=False,
        expected_container_id=previous["containers"]["bot"]["container_id"],
        expected_transaction_id=transaction_id,
    )

    deleted = [
        path
        for method, path, _body in runtime.requests
        if method == "DELETE"
    ]
    assert deleted == [f"/containers/{'c' * 64}?v=0&force=0"]


@pytest.mark.asyncio
async def test_socket_upgrade_restore_authorizes_transaction_labeled_container():
    """restore_images with the transaction id may replace the transaction's
    target containers even though their ids differ from the captured identity.

    This is the rollback-after-takeover path: the target containers were
    created by the target Manager's process (never this executor), so neither
    the captured id nor ``_created`` matches — only the transaction label.
    """
    runtime = Runtime()
    executor = DockerSocketUpgradeExecutor(runtime)
    records = [
        {
            "role": "bot",
            "reference": "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
            "image_id": BOT_NEW_ID,
        },
        {
            "role": "dashboard",
            "reference": "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0",
            "image_id": DASHBOARD_NEW_ID,
        },
    ]
    previous = await executor.capture_images(records)
    transaction_id = "t" * 32
    # The bot name is now held by the transaction-labeled target container
    # (created by the target Manager); the dashboard still holds the source.
    labeled = _inspect("bot", image="old-bot")
    labeled["Config"]["Labels"]["io.dicepp.upgrade-transaction"] = transaction_id
    runtime.extra_identities["c" * 64] = labeled
    runtime.current["dicepp"] = "c" * 64

    result = await executor.restore_images(
        previous, transaction_id=transaction_id
    )

    assert result["status"] == "restored"
    deleted = [
        path
        for method, path, _body in runtime.requests
        if method == "DELETE"
    ]
    assert deleted == [
        f"/containers/{'c' * 64}?v=0&force=0",
        f"/containers/{'d' * 64}?v=0&force=0",
    ]


@pytest.mark.asyncio
async def test_socket_upgrade_restore_refuses_unknown_name_holder():
    """restore_images without the transaction id must not delete a container
    whose id differs from the captured identity and carries no label."""
    runtime = Runtime()
    executor = DockerSocketUpgradeExecutor(runtime)
    records = [
        {
            "role": "bot",
            "reference": "ghcr.io/pear-studio/nonebot-dicepp:v3.1.0",
            "image_id": BOT_NEW_ID,
        },
        {
            "role": "dashboard",
            "reference": "ghcr.io/pear-studio/dicepp-dashboard:v3.1.0",
            "image_id": DASHBOARD_NEW_ID,
        },
    ]
    previous = await executor.capture_images(records)
    # An unrelated container (no labels) now holds the official bot name.
    runtime.current["dicepp"] = "c" * 64

    with pytest.raises(DockerRuntimeError, match="identity"):
        await executor.restore_images(previous)

    assert all(
        method != "DELETE" for method, _path, _body in runtime.requests
    )
