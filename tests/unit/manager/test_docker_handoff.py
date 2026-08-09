"""Docker handoff primitives: exact identity, rename, labels, restart, tag.

保护的行为契约：
- 精确身份提取失败时 fail closed（缺字段/非法 Image/非法 labels）；
- 容器操作只能通过精确名称/ID 寻址，模糊匹配拒绝；
- create 只调整 Labels 与 RestartPolicy，其余配置原样透传；
- tag/restart policy 只接受受控输入。
"""

from __future__ import annotations

import json
import urllib.parse

import pytest

from dicepp_manager.docker_handoff import (
    DockerHandoffExecutor,
    identity_from_payload,
)
from dicepp_manager.docker_runtime import DockerRuntimeError

MANAGER_OLD_ID = "sha256:" + ("1" * 64)
MANAGER_NEW_ID = "sha256:" + ("2" * 64)
TX = "a" * 32


def _inspect(
    *,
    name: str = "dicepp-manager",
    image_id: str = MANAGER_OLD_ID,
    running: bool = True,
    restart: str = "unless-stopped",
    labels: dict[str, str] | None = None,
    binds: list[str] | None = None,
    mounts: list[dict] | None = None,
    hostname: str = "c" * 12,
):
    merged = {
        "com.docker.compose.project": "dicepp",
        "com.docker.compose.service": "manager",
        "io.dicepp.managed": "false",
    }
    if labels:
        merged.update(labels)
    return {
        "Id": ("c" * 64),
        "Image": image_id,
        "Name": f"/{name}",
        "Config": {
            "Hostname": hostname,
            "Image": "ghcr.io/pear-studio/dicepp-dashboard:" + ("old" if image_id == MANAGER_OLD_ID else "new"),
            "Cmd": ["python", "-m", "dicepp_manager"],
            "Labels": merged,
        },
        "HostConfig": {
            "Binds": binds
            if binds is not None
            else [
                "/srv/dicepp/manager:/app/manager:rw",
                "/var/run/docker.sock:/var/run/docker.sock:rw",
            ],
            "RestartPolicy": {"Name": restart},
        },
        "Mounts": mounts
        if mounts is not None
        else [
            {
                "Type": "bind",
                "Source": "/srv/dicepp/manager",
                "Destination": "/app/manager",
                "Mode": "rw",
                "RW": True,
                "Propagation": "rprivate",
            },
            {
                "Type": "bind",
                "Source": "/var/run/docker.sock",
                "Destination": "/var/run/docker.sock",
                "Mode": "rw",
                "RW": True,
                "Propagation": "rprivate",
            },
        ],
        "NetworkSettings": {"Networks": {}},
        "State": {"Running": running},
    }


class Runtime:
    def __init__(self) -> None:
        self.requests: list[tuple] = []
        self.expected_statuses: list[set[int]] = []
        self.containers: dict[str, dict] = {
            "c" * 64: _inspect(),
        }

    async def _stop_container(self, container_id, *, grace_seconds=30):
        await self._request(
            "POST",
            f"/containers/{container_id}/stop?t={grace_seconds}",
            expected={204, 304},
        )

    async def _request(self, method, path, *, expected, raw=False, json_body=None):
        self.requests.append((method, path, json_body))
        self.expected_statuses.append(expected)
        if path.startswith("/containers/") and path.endswith("/json"):
            container_id = path.removeprefix("/containers/").removesuffix("/json")
            matches = [
                payload
                for identity, payload in self.containers.items()
                if identity.startswith(container_id)
            ]
            if len(matches) != 1:
                raise DockerRuntimeError("container not found")
            return matches[0]
        if path == "/containers/json?all=1&filters=" + urllib.parse.quote(
            json.dumps({"name": ["^/dicepp-manager$"]})
        ):
            return [{"Id": "c" * 64}]
        if path == "/containers/json?all=1&filters=" + urllib.parse.quote(
            json.dumps({"label": [f"io.dicepp.upgrade-transaction={TX}"]})
        ):
            return [{"Id": "d" * 64}]
        if path == "/containers/create?name=dicepp-manager":
            return {"Id": "d" * 64}
        return {"status": "ok"}


@pytest.fixture
def executor() -> DockerHandoffExecutor:
    return DockerHandoffExecutor(Runtime())


class TestIdentity:
    def test_identity_extracts_exact_facts(self) -> None:
        identity = identity_from_payload(_inspect())
        assert identity.container_id == "c" * 64
        assert identity.name == "dicepp-manager"
        assert identity.image_id == MANAGER_OLD_ID
        assert identity.running is True
        assert identity.restart_policy == "unless-stopped"
        assert identity.compose_project == "dicepp"
        assert identity.compose_service == "manager"
        assert identity.hostname == "c" * 12
        assert len(identity.mounts) == 2

    def test_identity_rejects_non_immutable_image(self) -> None:
        payload = _inspect()
        payload["Image"] = "sha256:xyz"
        with pytest.raises(DockerRuntimeError):
            identity_from_payload(payload)

    def test_identity_rejects_invalid_labels(self) -> None:
        payload = _inspect()
        payload["Config"]["Labels"] = {"io.dicepp.managed": 42}
        with pytest.raises(DockerRuntimeError):
            identity_from_payload(payload)

    def test_identity_rejects_incomplete_payload(self) -> None:
        payload = _inspect()
        del payload["State"]
        with pytest.raises(DockerRuntimeError):
            identity_from_payload(payload)

    def test_identity_rejects_missing_mount_identity(self) -> None:
        payload = _inspect()
        del payload["Mounts"]
        with pytest.raises(DockerRuntimeError, match="incomplete"):
            identity_from_payload(payload)


class TestPrimitives:
    async def test_delete_only_accepts_missing_when_explicitly_bound(
        self, executor
    ) -> None:
        container_id = "c" * 64

        await executor.delete(container_id)
        assert executor.runtime.expected_statuses[-1] == {204}

        await executor.delete(container_id, missing_ok=True)
        assert executor.runtime.expected_statuses[-1] == {204, 404}

    async def test_create_merges_labels_and_restart_policy(self, executor) -> None:
        config = {
            "Image": MANAGER_NEW_ID,
            "Cmd": ["python", "-m", "dicepp_manager"],
            "Labels": {"io.dicepp.managed": "false"},
            "HostConfig": {"Binds": ["/var/run/docker.sock:/var/run/docker.sock"]},
        }
        container_id = await executor.create(
            "dicepp-manager",
            config,
            extra_labels={
                "io.dicepp.upgrade-transaction": TX,
                "io.dicepp.upgrade-role": "manager",
            },
            restart_policy="no",
        )
        assert container_id == "d" * 64
        request = [entry for entry in executor.runtime.requests if entry[0] == "POST" and "/create" in entry[1]][0]
        body = request[2]
        assert body["Labels"]["io.dicepp.upgrade-transaction"] == TX
        assert body["Labels"]["io.dicepp.managed"] == "false"
        assert body["HostConfig"]["RestartPolicy"] == {"Name": "no"}
        assert body["Image"] == MANAGER_NEW_ID

    async def test_set_restart_policy_sends_update(self, executor) -> None:
        await executor.set_restart_policy("c" * 64, "no")
        request = executor.runtime.requests[-1]
        assert request[0] == "POST" and request[1].endswith("/update")
        assert request[2] == {"RestartPolicy": {"Name": "no"}}

    async def test_set_restart_policy_rejects_unknown_value(self, executor) -> None:
        with pytest.raises(DockerRuntimeError):
            await executor.set_restart_policy("c" * 64, "sometimes")

    async def test_rename_quotes_the_target_name(self, executor) -> None:
        await executor.rename("c" * 64, "dicepp-manager." + TX[:8])
        request = executor.runtime.requests[-1]
        assert "/rename?name=dicepp-manager." + TX[:8] in request[1]

    async def test_list_by_label_returns_matching_ids(self, executor) -> None:
        ids = await executor.list_by_label("io.dicepp.upgrade-transaction", TX)
        assert ids == ["d" * 64]

    async def test_tag_image_requires_pear_registry(self, executor) -> None:
        with pytest.raises(DockerRuntimeError):
            await executor.tag_image(MANAGER_NEW_ID, "quay.io/evil/dicepp", "dicepp-current")

    async def test_inspect_returns_verified_identity(self, executor) -> None:
        identity = await executor.inspect("c" * 64)
        assert identity.name == "dicepp-manager"
        assert identity.image_id == MANAGER_OLD_ID

    async def test_inspect_current_binds_process_to_exact_container(
        self, executor
    ) -> None:
        identity = await executor.inspect_current("c" * 12)

        assert identity.container_id == "c" * 64

    async def test_inspect_current_rejects_stale_configured_hostname(self) -> None:
        runtime = Runtime()
        runtime.containers["c" * 64] = _inspect(hostname="d" * 12)
        executor = DockerHandoffExecutor(runtime)

        with pytest.raises(DockerRuntimeError, match="does not match"):
            await executor.inspect_current("c" * 12)

    async def test_ambiguous_name_list_fails_closed(self) -> None:
        class AmbiguousRuntime(Runtime):
            async def _request(self, method, path, *, expected, raw=False, json_body=None):
                if "containers/json" in path and "name" in path:
                    return [{"Id": "a" * 64}, {"Id": "b" * 64}]
                return await super()._request(method, path, expected=expected, raw=raw, json_body=json_body)

        executor = DockerHandoffExecutor(AmbiguousRuntime())
        with pytest.raises(DockerRuntimeError, match="ambiguous"):
            await executor.list_by_name("dicepp-manager")


class TestHostBindResolution:
    def test_ignores_unrelated_named_volume_when_confirming_host_bind(self) -> None:
        identity = identity_from_payload(
            _inspect(
                binds=[
                    "dicepp-cache:/var/cache/dicepp:rw",
                    "/srv/dicepp/manager:/app/manager:rw",
                ]
            )
        )

        source = DockerHandoffExecutor.resolve_host_bind_source(
            identity,
            container_root="/app/manager/recovery",
            container_path=f"/app/manager/recovery/{TX}",
        )

        assert source == f"/srv/dicepp/manager/recovery/{TX}"

    def test_uses_longest_writable_bind_and_returns_host_subpath(self) -> None:
        payload = _inspect(
            binds=[
                "/srv/dicepp:/app:rw",
                "/srv/dicepp/manager:/app/manager:rw,rprivate",
            ],
            mounts=[
                {
                    "Type": "bind",
                    "Source": "/srv/dicepp",
                    "Destination": "/app",
                    "RW": True,
                },
                {
                    "Type": "bind",
                    "Source": "/srv/dicepp/manager",
                    "Destination": "/app/manager",
                    "RW": True,
                },
            ],
        )
        identity = identity_from_payload(payload)

        source = DockerHandoffExecutor.resolve_host_bind_source(
            identity,
            container_root="/app/manager/recovery",
            container_path=f"/app/manager/recovery/{TX}",
        )

        assert source == f"/srv/dicepp/manager/recovery/{TX}"

    @pytest.mark.parametrize(
        "container_path",
        [
            f"/app/manager/recovery/../{TX}",
            f"/app/manager/recovery-other/{TX}",
            f"relative/recovery/{TX}",
        ],
    )
    def test_rejects_path_outside_canonical_recovery_root(
        self, container_path: str
    ) -> None:
        identity = identity_from_payload(_inspect())

        with pytest.raises(DockerRuntimeError):
            DockerHandoffExecutor.resolve_host_bind_source(
                identity,
                container_root="/app/manager/recovery",
                container_path=container_path,
            )

    @pytest.mark.parametrize(
        ("mount_type", "read_write"),
        [("volume", True), ("bind", False)],
    )
    def test_rejects_non_writable_or_non_bind_controlling_mount(
        self, mount_type: str, read_write: bool
    ) -> None:
        identity = identity_from_payload(
            _inspect(
                mounts=[
                    {
                        "Type": "bind",
                        "Source": "/srv/dicepp/manager",
                        "Destination": "/app/manager",
                        "RW": True,
                    },
                    {
                        "Type": mount_type,
                        "Source": "/srv/transaction",
                        "Destination": f"/app/manager/recovery/{TX}",
                        "RW": read_write,
                    },
                ],
                binds=[
                    "/srv/dicepp/manager:/app/manager:rw",
                    f"/srv/transaction:/app/manager/recovery/{TX}:rw",
                ],
            )
        )

        with pytest.raises(DockerRuntimeError, match="writable Docker bind"):
            DockerHandoffExecutor.resolve_host_bind_source(
                identity,
                container_root="/app/manager/recovery",
                container_path=f"/app/manager/recovery/{TX}",
            )

    def test_rejects_writable_nested_bind_shadowing_transaction(self) -> None:
        identity = identity_from_payload(
            _inspect(
                mounts=[
                    {
                        "Type": "bind",
                        "Source": "/srv/dicepp/manager",
                        "Destination": "/app/manager",
                        "RW": True,
                    },
                    {
                        "Type": "bind",
                        "Source": "/tmp/foreign-transaction",
                        "Destination": f"/app/manager/recovery/{TX}",
                        "RW": True,
                    },
                ],
                binds=[
                    "/srv/dicepp/manager:/app/manager:rw",
                    f"/tmp/foreign-transaction:/app/manager/recovery/{TX}:rw",
                ],
            )
        )

        with pytest.raises(DockerRuntimeError, match="shadowed"):
            DockerHandoffExecutor.resolve_host_bind_source(
                identity,
                container_root="/app/manager/recovery",
                container_path=f"/app/manager/recovery/{TX}",
            )

    @pytest.mark.parametrize(
        "binds",
        [
            ["/different/source:/app/manager:rw"],
            ["/srv/dicepp/manager:/app/manager:ro"],
            [
                "/srv/dicepp/manager:/app/manager:rw",
                "/srv/dicepp/manager:/app/manager:rw",
            ],
        ],
    )
    def test_rejects_configured_bind_disagreement(self, binds: list[str]) -> None:
        identity = identity_from_payload(_inspect(binds=binds))

        with pytest.raises(DockerRuntimeError, match="configured bind"):
            DockerHandoffExecutor.resolve_host_bind_source(
                identity,
                container_root="/app/manager/recovery",
                container_path=f"/app/manager/recovery/{TX}",
            )

    @pytest.mark.parametrize(
        "invalid_target_bind",
        [
            "dicepp-manager:/app/manager:rw",
            "/srv/dicepp/../manager:/app/manager:rw",
            "/srv/dicepp/manager:/app/manager:rw:unexpected",
        ],
    )
    def test_rejects_invalid_bind_that_targets_managed_destination(
        self, invalid_target_bind: str
    ) -> None:
        identity = identity_from_payload(
            _inspect(
                binds=[
                    "dicepp-cache:/var/cache/dicepp:rw",
                    invalid_target_bind,
                    "/srv/dicepp/manager:/app/manager:rw",
                ]
            )
        )

        with pytest.raises(DockerRuntimeError):
            DockerHandoffExecutor.resolve_host_bind_source(
                identity,
                container_root="/app/manager/recovery",
                container_path=f"/app/manager/recovery/{TX}",
            )

    def test_rejects_ambiguous_runtime_mounts(self) -> None:
        duplicate = {
            "Type": "bind",
            "Source": "/srv/dicepp/manager",
            "Destination": "/app/manager",
            "RW": True,
        }
        identity = identity_from_payload(
            _inspect(mounts=[duplicate, dict(duplicate)])
        )

        with pytest.raises(DockerRuntimeError, match="unique controlling"):
            DockerHandoffExecutor.resolve_host_bind_source(
                identity,
                container_root="/app/manager/recovery",
                container_path=f"/app/manager/recovery/{TX}",
            )
