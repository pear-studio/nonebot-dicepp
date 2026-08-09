"""Fixed Docker Engine operations used by Linux upgrade transactions."""

from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

from .docker_runtime import (
    DockerRuntimeError,
    DockerSocketRuntimeAdapter,
    _UnixSocketConnection,
)

_ROLE_IMAGES = {
    "bot": "nonebot-dicepp",
    "dashboard": "dicepp-dashboard",
}
_IMAGE_DEFAULT_KEYS = {
    "Cmd",
    "Entrypoint",
    "Env",
    "Healthcheck",
    "WorkingDir",
    "User",
    "ExposedPorts",
    "StopSignal",
    "Volumes",
}
_CONFIG_KEYS = {
    "Hostname",
    "Domainname",
    "AttachStdin",
    "AttachStdout",
    "AttachStderr",
    "Tty",
    "OpenStdin",
    "StdinOnce",
    "ArgsEscaped",
    "Labels",
    "StopTimeout",
}
_HOST_KEYS = {
    "Binds",
    "ContainerIDFile",
    "LogConfig",
    "NetworkMode",
    "PortBindings",
    "RestartPolicy",
    "AutoRemove",
    "VolumeDriver",
    "VolumesFrom",
    "ConsoleSize",
    "CapAdd",
    "CapDrop",
    "CgroupnsMode",
    "Dns",
    "DnsOptions",
    "DnsSearch",
    "ExtraHosts",
    "GroupAdd",
    "IpcMode",
    "Cgroup",
    "Links",
    "OomScoreAdj",
    "PidMode",
    "Privileged",
    "PublishAllPorts",
    "ReadonlyRootfs",
    "SecurityOpt",
    "UTSMode",
    "UsernsMode",
    "ShmSize",
    "Runtime",
    "Isolation",
    "MaskedPaths",
    "ReadonlyPaths",
    "Mounts",
    "Init",
}
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
#: Must match ``LinuxBundleUpgradeAdapter._tx_labels`` in upgrade.py.
_TRANSACTION_LABEL = "io.dicepp.upgrade-transaction"


class DockerSocketUpgradeExecutor:
    """Recreate only the existing Compose bot/dashboard containers.

    The captured container configuration is the source for binds, networks and
    labels; only ``Config.Image`` changes.  This avoids a registry pull and
    leaves the Manager container and Compose topology untouched.
    """

    def __init__(self, runtime: DockerSocketRuntimeAdapter) -> None:
        self.runtime = runtime
        #: Container ids created by this executor in the current process.
        #: A replace is authorized to delete them even when their id no longer
        #: matches the captured identity (same-transaction switch -> restore).
        self._created: set[str] = set()

    async def capture_images(
        self, image_records: list[dict[str, str]]
    ) -> dict[str, Any]:
        targets = _target_images(image_records)
        bot_id = await self.runtime._resolve_container(
            next(iter(self.runtime._allowed))
        )
        bot = await self.runtime._request(
            "GET", f"/containers/{bot_id}/json", expected={200}
        )
        labels = bot.get("Config", {}).get("Labels", {})
        project = labels.get("com.docker.compose.project")
        if not isinstance(project, str) or not project:
            raise DockerRuntimeError(
                "Managed Bot container has no Compose project identity"
            )
        dashboard_id = await self._resolve_compose_service(project, "dashboard")
        dashboard = await self.runtime._request(
            "GET", f"/containers/{dashboard_id}/json", expected={200}
        )
        containers = {
            "bot": await self._capture_with_image_defaults(bot),
            "dashboard": await self._capture_with_image_defaults(dashboard),
        }
        return {
            "project": project,
            "targets": targets,
            "containers": containers,
        }

    async def capture_manager(self, project: str) -> dict[str, Any]:
        """Capture the official Manager container under the Compose project."""
        manager_id = await self._resolve_compose_service(project, "manager")
        manager = await self.runtime._request(
            "GET", f"/containers/{manager_id}/json", expected={200}
        )
        captured = await self._capture_with_image_defaults(manager)
        labels = manager.get("Config", {}).get("Labels") or {}
        return {
            "container_id": captured["container_id"],
            "name": captured["name"],
            "image_id": captured["image_id"],
            "image_reference": captured["image_reference"],
            "running": captured["running"],
            "restart_policy": (
                (manager.get("HostConfig") or {}).get("RestartPolicy") or {}
            ).get("Name"),
            "labels": {str(k): str(v) for k, v in labels.items()},
        }

    async def inspect_tag(self, reference: str) -> dict[str, Any]:
        """Resolve a local repository tag to its immutable image identity."""
        payload = await self.runtime._request(
            "GET",
            "/images/" + urllib.parse.quote(reference, safe="") + "/json",
            expected={200},
        )
        if not isinstance(payload, dict):
            raise DockerRuntimeError("Docker image tag inspect payload is invalid")
        return payload

    async def inspect_tag_optional(self, reference: str) -> dict[str, Any] | None:
        """Resolve a local tag, returning ``None`` only for Docker HTTP 404."""
        try:
            return await self.inspect_tag(reference)
        except DockerRuntimeError as exc:
            if exc.detail.get("status_code") == 404:
                return None
            raise

    async def load_images(self, archive: Path) -> dict[str, Any]:
        result = await asyncio.to_thread(self._load_images_sync, archive)
        return {"status": "loaded", "response": result[-4000:]}

    async def resolve_images(
        self, image_records: list[dict[str, str]]
    ) -> dict[str, dict[str, Any]]:
        """Resolve verified mutable tags to immutable local image identities."""
        targets = _target_images(image_records)
        resolved: dict[str, dict[str, Any]] = {}
        for role, record in targets.items():
            payload = await self._inspect_image(record["reference"])
            image_id = payload.get("Id")
            if image_id != record["image_id"]:
                raise DockerRuntimeError(
                    f"Loaded {role} image identity differs from release manifest"
                )
            resolved[role] = {
                "reference": record["reference"],
                "image_id": image_id,
                "defaults": _image_defaults(payload),
            }
        return resolved

    async def switch_images(
        self,
        *,
        target_images: dict[str, dict[str, Any]],
        previous: dict[str, Any],
    ) -> dict[str, Any]:
        targets = _validated_resolved_images(target_images)
        switched: list[str] = []
        try:
            for role in ("bot", "dashboard"):
                await self._replace(
                    previous["containers"][role],
                    targets[role],
                    expected_container_id=previous["containers"][role][
                        "container_id"
                    ],
                )
                switched.append(role)
        except Exception:
            for role in reversed(switched):
                try:
                    await self._replace(
                        previous["containers"][role],
                        {
                            "image_id": previous["containers"][role]["image_id"],
                            "defaults": previous["containers"][role][
                                "image_defaults"
                            ],
                        },
                        expected_container_id=previous["containers"][role][
                            "container_id"
                        ],
                    )
                except Exception:
                    pass
            raise
        return {
            "status": "switched",
            "roles": switched,
            "images": {
                role: target["image_id"] for role, target in targets.items()
            },
        }

    async def restore_images(
        self,
        previous: dict[str, Any],
        *,
        transaction_id: str | None = None,
    ) -> dict[str, Any]:
        restored: list[str] = []
        for role in ("bot", "dashboard"):
            container = previous["containers"][role]
            await self._replace(
                container,
                {
                    "image_id": container["image_id"],
                    "defaults": container["image_defaults"],
                },
                expected_container_id=container["container_id"],
                expected_transaction_id=transaction_id,
            )
            restored.append(role)
        return {"status": "restored", "roles": restored}

    async def _capture_with_image_defaults(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        image_id = payload.get("Image")
        if not isinstance(image_id, str) or not _IMAGE_ID.fullmatch(image_id):
            raise DockerRuntimeError(
                "Docker container has no immutable image identity"
            )
        image = await self._inspect_image(image_id)
        if image.get("Id") != image_id:
            raise DockerRuntimeError("Docker image identity changed during capture")
        return _capture_container(payload, _image_defaults(image))

    async def _inspect_image(self, identity: str) -> dict[str, Any]:
        payload = await self.runtime._request(
            "GET",
            "/images/" + urllib.parse.quote(identity, safe="") + "/json",
            expected={200},
        )
        if not isinstance(payload, dict):
            raise DockerRuntimeError("Docker image inspect payload is invalid")
        return payload

    async def _resolve_compose_service(self, project: str, service: str) -> str:
        filters = json.dumps(
            {
                "label": [
                    f"com.docker.compose.project={project}",
                    f"com.docker.compose.service={service}",
                ]
            }
        )
        payload = await self.runtime._request(
            "GET",
            "/containers/json?all=1&filters=" + urllib.parse.quote(filters),
            expected={200},
        )
        if not isinstance(payload, list) or len(payload) != 1:
            raise DockerRuntimeError(
                f"Expected exactly one Compose {service} container"
            )
        container_id = payload[0].get("Id")
        if not isinstance(container_id, str) or not re.fullmatch(
            r"[0-9a-fA-F]{12,64}", container_id
        ):
            raise DockerRuntimeError("Docker returned an invalid container id")
        return container_id

    async def _replace(
        self,
        captured: dict[str, Any],
        image: dict[str, Any],
        *,
        extra_labels: dict[str, str] | None = None,
        restart_policy: str | None = None,
        start: bool = True,
        expected_container_id: str | None = None,
        expected_transaction_id: str | None = None,
    ) -> None:
        """Replace a captured container by name, verifying identity first.

        The existing container is stopped and deleted only when its identity
        is authorized: it matches ``expected_container_id`` (the captured
        identity), it was created by this executor in the current transaction
        (``_created``), or it carries ``expected_transaction_id`` in its
        ``io.dicepp.upgrade-transaction`` label.  Anything else fails closed
        and never reaches a DELETE.
        """
        config = dict(captured["config"])
        config.update(
            _explicit_image_overrides(
                captured["effective_image_config"],
                captured["image_defaults"],
                image["defaults"],
            )
        )
        config["Image"] = image["image_id"]
        if extra_labels:
            labels = dict(config.get("Labels") or {})
            labels.update(extra_labels)
            config["Labels"] = labels
        config["HostConfig"] = dict(captured["host_config"])
        if restart_policy is not None:
            config["HostConfig"]["RestartPolicy"] = {"Name": restart_policy}
        config["NetworkingConfig"] = {
            "EndpointsConfig": dict(captured["endpoints"])
        }
        current_id = await self._find_by_name(captured["name"])
        if current_id is not None:
            if not await self._authorized_to_replace(
                current_id,
                expected_container_id=expected_container_id,
                expected_transaction_id=expected_transaction_id,
            ):
                raise DockerRuntimeError(
                    f"Refusing to replace container {captured['name']}: "
                    "existing identity does not match the captured or "
                    "transaction identity"
                )
            await self.runtime._stop_container(current_id, grace_seconds=30)
            await self.runtime._request(
                "DELETE",
                f"/containers/{current_id}?v=0&force=0",
                expected={204, 404},
            )
        created = await self.runtime._request(
            "POST",
            "/containers/create?name="
            + urllib.parse.quote(captured["name"], safe=""),
            expected={201},
            json_body=config,
        )
        container_id = created.get("Id") if isinstance(created, dict) else None
        if not isinstance(container_id, str):
            raise DockerRuntimeError("Docker did not return a created container id")
        self._created.add(container_id)
        if start and captured["running"]:
            await self.runtime._request(
                "POST", f"/containers/{container_id}/start", expected={204, 304}
            )

    async def _authorized_to_replace(
        self,
        current_id: str,
        *,
        expected_container_id: str | None,
        expected_transaction_id: str | None,
    ) -> bool:
        if expected_container_id is not None and current_id == expected_container_id:
            return True
        if current_id in self._created:
            return True
        if expected_transaction_id is not None:
            payload = await self._inspect_container(current_id)
            labels = payload.get("Config", {}).get("Labels") or {}
            if not isinstance(labels, dict):
                labels = {}
            if labels.get(_TRANSACTION_LABEL) == expected_transaction_id:
                return True
        return False

    async def _inspect_container(self, container_id: str) -> dict[str, Any]:
        payload = await self.runtime._request(
            "GET",
            f"/containers/{container_id}/json",
            expected={200},
        )
        if not isinstance(payload, dict):
            raise DockerRuntimeError("Docker inspect payload is invalid")
        return payload

    async def _find_by_name(self, name: str) -> str | None:
        filters = json.dumps({"name": [f"^/{name}$"]})
        payload = await self.runtime._request(
            "GET",
            "/containers/json?all=1&filters=" + urllib.parse.quote(filters),
            expected={200},
        )
        if not isinstance(payload, list):
            raise DockerRuntimeError("Docker container list is invalid")
        if not payload:
            return None
        if len(payload) != 1 or not isinstance(payload[0].get("Id"), str):
            raise DockerRuntimeError(f"Container identity is ambiguous: {name}")
        return payload[0]["Id"]

    def _load_images_sync(self, archive: Path) -> str:
        if not archive.is_file() or archive.is_symlink():
            raise DockerRuntimeError("Docker image archive is not a regular file")
        connection = _UnixSocketConnection(
            self.runtime._socket_path, self.runtime._timeout
        )
        try:
            with archive.open("rb") as source:
                connection.request(
                    "POST",
                    "/images/load?quiet=1",
                    body=source,
                    headers={
                        "Content-Type": "application/x-tar",
                        "Content-Length": str(archive.stat().st_size),
                    },
                )
                response = connection.getresponse()
                body = response.read()
        except OSError as exc:
            raise DockerRuntimeError(f"Docker image load failed: {exc}") from exc
        finally:
            connection.close()
        text = body.decode("utf-8", errors="replace")
        if response.status != 200:
            raise DockerRuntimeError(
                f"Docker image load returned HTTP {response.status}: {text[-4000:]}"
            )
        return text


def _target_images(
    values: list[dict[str, str]],
) -> dict[str, dict[str, str]]:
    if not isinstance(values, list):
        raise DockerRuntimeError("Release image records are invalid")
    result: dict[str, dict[str, str]] = {}
    for value in values:
        if not isinstance(value, dict) or set(value) != {
            "role",
            "reference",
            "image_id",
        }:
            raise DockerRuntimeError("Release image record is invalid")
        role = value["role"]
        reference = value["reference"]
        image_id = value["image_id"]
        if (
            role not in _ROLE_IMAGES
            or role in result
            or not isinstance(reference, str)
            or _ROLE_IMAGES[role] not in reference
            or not isinstance(image_id, str)
            or not _IMAGE_ID.fullmatch(image_id)
        ):
            raise DockerRuntimeError("Release image record identity is invalid")
        result[role] = dict(value)
    if set(result) != set(_ROLE_IMAGES):
        raise DockerRuntimeError("Release must contain bot and dashboard images")
    if len({value["reference"] for value in result.values()}) != len(result):
        raise DockerRuntimeError("Release image references are not distinct")
    if len({value["image_id"] for value in result.values()}) != len(result):
        raise DockerRuntimeError("Release image identities are not distinct")
    return result


def _validated_resolved_images(
    values: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if not isinstance(values, dict) or set(values) != set(_ROLE_IMAGES):
        raise DockerRuntimeError("Resolved release images are incomplete")
    for role, value in values.items():
        if (
            not isinstance(value, dict)
            or not _IMAGE_ID.fullmatch(str(value.get("image_id", "")))
            or not isinstance(value.get("defaults"), dict)
        ):
            raise DockerRuntimeError(f"Resolved {role} image is invalid")
    return values


def _capture_container(
    payload: dict[str, Any], image_defaults: dict[str, Any]
) -> dict[str, Any]:
    container_id = payload.get("Id")
    name = str(payload.get("Name", "")).removeprefix("/")
    config = payload.get("Config")
    host = payload.get("HostConfig")
    networks = payload.get("NetworkSettings", {}).get("Networks")
    state = payload.get("State")
    if (
        not isinstance(container_id, str)
        or not name
        or not isinstance(config, dict)
        or not isinstance(host, dict)
        or not isinstance(networks, dict)
        or not isinstance(state, dict)
        or not isinstance(config.get("Image"), str)
        or not isinstance(payload.get("Image"), str)
        or not _IMAGE_ID.fullmatch(payload["Image"])
    ):
        raise DockerRuntimeError("Docker inspect payload is incomplete")
    _reject_unhandled_nondefault(
        config,
        _CONFIG_KEYS | _IMAGE_DEFAULT_KEYS | {"Image"},
        "container Config",
    )
    _reject_unhandled_nondefault(host, _HOST_KEYS, "HostConfig")
    endpoints = {}
    for network, endpoint in networks.items():
        if not isinstance(network, str) or not isinstance(endpoint, dict):
            raise DockerRuntimeError("Docker network identity is invalid")
        endpoints[network] = {
            key: endpoint[key]
            for key in ("IPAMConfig", "Links", "Aliases", "DriverOpts")
            if key in endpoint and endpoint[key] is not None
        }
    return {
        "container_id": container_id,
        "name": name,
        "image_reference": config["Image"],
        "image_id": payload["Image"],
        "image_defaults": image_defaults,
        "running": state.get("Running") is True,
        "config": {key: config[key] for key in _CONFIG_KEYS if key in config},
        "effective_image_config": {
            key: config.get(key) for key in _IMAGE_DEFAULT_KEYS
        },
        "host_config": {key: host[key] for key in _HOST_KEYS if key in host},
        "endpoints": endpoints,
    }


def _image_defaults(payload: dict[str, Any]) -> dict[str, Any]:
    config = payload.get("Config")
    if not isinstance(config, dict):
        raise DockerRuntimeError("Docker image defaults are unavailable")
    _reject_unhandled_nondefault(
        config,
        _IMAGE_DEFAULT_KEYS
        | {
            "ArgsEscaped",
            "AttachStderr",
            "AttachStdin",
            "AttachStdout",
            "Domainname",
            "Hostname",
            "Labels",
            "OnBuild",
            "OpenStdin",
            "StdinOnce",
            "Tty",
        },
        "image Config",
    )
    return {key: config.get(key) for key in _IMAGE_DEFAULT_KEYS}


def _explicit_image_overrides(
    effective: dict[str, Any],
    old_defaults: dict[str, Any],
    new_defaults: dict[str, Any],
) -> dict[str, Any]:
    """Derive only explicit container overrides from the old image merge.

    Docker will merge these values with ``new_defaults`` when it creates the
    replacement container.  Ambiguous removal/merge cases are refused.
    """
    if not all(
        isinstance(value, dict)
        for value in (effective, old_defaults, new_defaults)
    ):
        raise DockerRuntimeError("Image defaults cannot be compared safely")
    changed_defaults = [
        key
        for key in _IMAGE_DEFAULT_KEYS
        if old_defaults.get(key) != new_defaults.get(key)
    ]
    if changed_defaults:
        # Docker inspect exposes only the effective container Config.  It
        # cannot tell whether a value equal to the old image default was also
        # explicitly pinned by Compose.  Re-inferring overrides would silently
        # drop such a pin when the new default changes, so phase one must fail
        # closed until the Compose source is carried into this boundary.
        raise DockerRuntimeError(
            "Image defaults changed without a provable Compose override source "
            f"({', '.join(sorted(changed_defaults))}); manual upgrade required"
        )
    overrides: dict[str, Any] = {}
    for key in _IMAGE_DEFAULT_KEYS:
        current = effective.get(key)
        old = old_defaults.get(key)
        new = new_defaults.get(key)
        if key == "Env":
            explicit = _derive_env_overrides(current, old, new)
            if explicit:
                overrides[key] = explicit
        elif key in {"ExposedPorts", "Volumes"}:
            explicit_map = _derive_map_overrides(key, current, old, new)
            if explicit_map:
                overrides[key] = explicit_map
        elif current != old:
            _validate_default_shape(key, current, old, new)
            overrides[key] = current
    return overrides


def _derive_env_overrides(current: Any, old: Any, new: Any) -> list[str]:
    current_map = _env_map(current)
    old_map = _env_map(old)
    _env_map(new)
    if not set(old_map).issubset(current_map):
        raise DockerRuntimeError(
            "Container removes old image environment defaults; manual upgrade required"
        )
    return [
        entry
        for entry in (current or [])
        if old_map.get(entry.partition("=")[0]) != entry
    ]


def _env_map(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, list) or any(
        not isinstance(item, str)
        or not item.partition("=")[0]
        or "=" not in item
        for item in value
    ):
        raise DockerRuntimeError(
            "Image environment defaults cannot be merged safely"
        )
    result: dict[str, str] = {}
    for item in value:
        name = item.partition("=")[0]
        if name in result:
            raise DockerRuntimeError("Image environment has duplicate keys")
        result[name] = item
    return result


def _derive_map_overrides(
    key: str, current: Any, old: Any, new: Any
) -> dict[str, Any]:
    maps: list[dict[str, Any]] = []
    for value in (current, old, new):
        if value is None:
            maps.append({})
        elif isinstance(value, dict):
            maps.append(value)
        else:
            raise DockerRuntimeError(
                f"Image {key} defaults cannot be merged safely"
            )
    current_map, old_map, _new_map = maps
    if not set(old_map).issubset(current_map):
        raise DockerRuntimeError(
            f"Container removes old image {key} defaults; manual upgrade required"
        )
    return {
        name: value
        for name, value in current_map.items()
        if name not in old_map or old_map[name] != value
    }


def _validate_default_shape(
    key: str, current: Any, old: Any, new: Any
) -> None:
    if key in {"Cmd", "Entrypoint"}:
        allowed = lambda value: value is None or (
            isinstance(value, list)
            and all(isinstance(item, str) for item in value)
        )
    elif key == "Healthcheck":
        allowed = lambda value: value is None or isinstance(value, dict)
    elif key in {"WorkingDir", "User", "StopSignal"}:
        allowed = lambda value: value is None or isinstance(value, str)
    else:
        allowed = lambda _value: True
    if not all(allowed(value) for value in (current, old, new)):
        raise DockerRuntimeError(
            f"Image {key} defaults cannot be merged safely"
        )


def _reject_unhandled_nondefault(
    value: dict[str, Any], handled: set[str], label: str
) -> None:
    unsafe = sorted(
        key
        for key, item in value.items()
        if key not in handled and item not in (None, False, 0, "", [], {})
    )
    if unsafe:
        raise DockerRuntimeError(
            f"{label} contains unsupported non-default fields: {unsafe}"
        )
