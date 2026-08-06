"""Docker primitives for the Linux Manager handoff transaction.

This module only performs exact-identity Docker Engine operations needed to
switch the Manager container: inspect, rename (never rebuild the source
container), create the target with transaction labels, restart-policy
updates and local ``dicepp-current`` tag moves.  The authority decision
(who may touch which container) lives in the helper / adapter flow, not
here; these primitives only expose identity extraction and verification
helpers so the flow can enforce it precisely.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Mapping

from .docker_runtime import (
    DockerRuntimeError,
    DockerSocketRuntimeAdapter,
)

_IMAGE_OR_CONTAINER_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_CONTAINER_ID = re.compile(r"^[0-9a-fA-F]{12,64}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class ContainerIdentity:
    """Verified facts about one container, for exact-match authorization."""

    container_id: str
    name: str
    image_id: str
    image_reference: str
    running: bool
    restart_policy: str | None
    labels: Mapping[str, str] = field(default_factory=dict)
    config: Mapping[str, Any] = field(default_factory=dict)
    host_config: Mapping[str, Any] = field(default_factory=dict)
    networks: Mapping[str, Any] = field(default_factory=dict)
    hostname: str | None = None
    mounts: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    @property
    def compose_project(self) -> str | None:
        return self.labels.get("com.docker.compose.project")

    @property
    def compose_service(self) -> str | None:
        return self.labels.get("com.docker.compose.service")


class DockerHandoffExecutor:
    """Fixed Engine operations used by the Manager switch helper."""

    def __init__(self, runtime: DockerSocketRuntimeAdapter) -> None:
        self.runtime = runtime

    async def inspect(self, container_id: str) -> ContainerIdentity:
        identity = await self._inspect_payload(container_id)
        return identity_from_payload(identity)

    async def inspect_current(self, hostname: str) -> ContainerIdentity:
        """Inspect the container running this process and bind it to its hostname.

        Docker's default container hostname is its short container id.  The
        Manager handoff uses that fact to prove that the process making a
        takeover decision is the exact container returned by Engine inspect,
        rather than merely another container running the same version.
        """
        if not _CONTAINER_ID.fullmatch(hostname):
            raise DockerRuntimeError("Current container hostname is invalid")
        identity = await self.inspect(hostname)
        normalized = hostname.lower()
        if (
            not identity.container_id.lower().startswith(normalized)
            or identity.hostname is None
            or identity.hostname.lower() != normalized
        ):
            raise DockerRuntimeError(
                "Current process hostname does not match Docker container identity"
            )
        return identity

    @staticmethod
    def resolve_host_bind_source(
        identity: ContainerIdentity,
        *,
        container_root: str | PurePosixPath,
        container_path: str | PurePosixPath,
    ) -> str:
        """Map a trusted container path to its exact writable host bind source.

        Runtime ``Mounts`` determines which mount actually controls the path.
        ``HostConfig.Binds`` independently confirms the configured source,
        destination and write mode.  A missing, read-only, non-bind, shadowed
        or ambiguous mapping fails closed.
        """
        root = _clean_absolute_linux_path(container_root, label="container root")
        target = _clean_absolute_linux_path(container_path, label="container path")
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise DockerRuntimeError(
                "Container path escapes the trusted recovery root"
            ) from exc

        controlling: list[tuple[PurePosixPath, Mapping[str, Any]]] = []
        greatest_depth = -1
        for raw_mount in identity.mounts:
            if not isinstance(raw_mount, Mapping):
                raise DockerRuntimeError("Docker mount identity is invalid")
            destination = _clean_absolute_linux_path(
                raw_mount.get("Destination"), label="mount destination"
            )
            try:
                target.relative_to(destination)
            except ValueError:
                continue
            depth = len(destination.parts)
            if depth > greatest_depth:
                greatest_depth = depth
                controlling = [(destination, raw_mount)]
            elif depth == greatest_depth:
                controlling.append((destination, raw_mount))

        if len(controlling) != 1:
            raise DockerRuntimeError(
                "Container path has no unique controlling Docker mount"
            )
        destination, raw_mount = controlling[0]
        if raw_mount.get("Type") != "bind" or raw_mount.get("RW") is not True:
            raise DockerRuntimeError(
                "Container path is not backed by a writable Docker bind mount"
            )
        if destination != root.parent:
            raise DockerRuntimeError(
                "Recovery transaction is shadowed outside the managed root bind"
            )
        source = _clean_absolute_linux_path(
            raw_mount.get("Source"), label="mount source"
        )

        raw_binds = identity.host_config.get("Binds")
        if not isinstance(raw_binds, list):
            raise DockerRuntimeError("Docker bind configuration is unavailable")
        matching_binds = []
        destination_text = str(destination)
        for raw_bind in raw_binds:
            if not isinstance(raw_bind, str):
                raise DockerRuntimeError("Docker bind configuration is invalid")
            # HostConfig.Binds also contains named volumes.  Their sources are
            # intentionally not absolute host paths, so only parse entries that
            # claim to target the controlling runtime mount.  A malformed entry
            # mentioning that destination still reaches the strict parser and
            # therefore fails closed.
            if destination_text not in raw_bind.split(":")[1:]:
                continue
            bind_source, bind_destination, writable = _parse_linux_bind(raw_bind)
            if bind_source == source and bind_destination == destination:
                matching_binds.append(writable)
        if matching_binds != [True]:
            raise DockerRuntimeError(
                "Docker runtime mount does not match one writable configured bind"
            )

        relative = target.relative_to(destination)
        host_path = source.joinpath(relative)
        try:
            host_path.relative_to(source)
        except ValueError as exc:
            raise DockerRuntimeError("Resolved host bind path escapes its source") from exc
        return str(host_path)

    async def _inspect_payload(self, container_id: str) -> dict[str, Any]:
        if not _CONTAINER_ID.fullmatch(container_id):
            raise DockerRuntimeError("Docker container id is invalid")
        payload = await self.runtime._request(
            "GET",
            f"/containers/{container_id}/json",
            expected={200},
        )
        if not isinstance(payload, dict):
            raise DockerRuntimeError("Docker inspect payload is invalid")
        return payload

    async def list_by_name(self, name: str) -> str | None:
        """Return the exact container id for *name*, or None."""
        if not _SAFE_NAME.fullmatch(name):
            raise DockerRuntimeError("Docker container name is invalid")
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

    async def list_by_label(self, key: str, value: str) -> list[str]:
        """Return container ids carrying exactly ``key=value``."""
        if not _SAFE_NAME.fullmatch(key) or not _SAFE_NAME.fullmatch(value):
            raise DockerRuntimeError("Docker label query is invalid")
        filters = json.dumps({"label": [f"{key}={value}"]})
        payload = await self.runtime._request(
            "GET",
            "/containers/json?all=1&filters=" + urllib.parse.quote(filters),
            expected={200},
        )
        if not isinstance(payload, list):
            raise DockerRuntimeError("Docker container list is invalid")
        result: list[str] = []
        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("Id"), str):
                raise DockerRuntimeError("Docker container list item is invalid")
            result.append(item["Id"])
        return result

    async def stop(self, container_id: str) -> None:
        await self.runtime._request(
            "POST",
            f"/containers/{container_id}/stop?t=30",
            expected={204, 304},
        )

    async def start(self, container_id: str) -> None:
        await self.runtime._request(
            "POST",
            f"/containers/{container_id}/start",
            expected={204, 304},
        )

    async def delete(self, container_id: str) -> None:
        await self.runtime._request(
            "DELETE",
            f"/containers/{container_id}?v=0&force=0",
            expected={204},
        )

    async def rename(self, container_id: str, new_name: str) -> None:
        if not _SAFE_NAME.fullmatch(new_name):
            raise DockerRuntimeError("Docker container name is invalid")
        await self.runtime._request(
            "POST",
            "/containers/"
            + container_id
            + "/rename?name="
            + urllib.parse.quote(new_name, safe=""),
            expected={204},
        )

    async def create(
        self,
        name: str,
        config: Mapping[str, Any],
        *,
        extra_labels: Mapping[str, str],
        restart_policy: str,
    ) -> str:
        """Create a container with transaction labels and restart=no.

        ``config`` is the captured source configuration (Image replaced by
        the caller); only labels and RestartPolicy are adjusted here.
        """
        if not _SAFE_NAME.fullmatch(name):
            raise DockerRuntimeError("Docker container name is invalid")
        body = dict(config)
        labels = dict(body.get("Labels") or {})
        labels.update(extra_labels)
        body["Labels"] = labels
        host = dict(body.get("HostConfig") or {})
        host["RestartPolicy"] = {"Name": restart_policy}
        body["HostConfig"] = host
        created = await self.runtime._request(
            "POST",
            "/containers/create?name=" + urllib.parse.quote(name, safe=""),
            expected={201},
            json_body=body,
        )
        container_id = created.get("Id") if isinstance(created, dict) else None
        if not isinstance(container_id, str):
            raise DockerRuntimeError("Docker did not return a created container id")
        return container_id

    async def set_restart_policy(self, container_id: str, policy: str) -> None:
        """Dynamically change the restart policy of an existing container."""
        if not re.fullmatch(r"^(no|always|unless-stopped|on-failure|on-failure:[0-9]+)$", policy):
            raise DockerRuntimeError("Docker restart policy is invalid")
        await self.runtime._request(
            "POST",
            f"/containers/{container_id}/update",
            expected={200},
            json_body={"RestartPolicy": {"Name": policy}},
        )

    async def tag_image(self, image_id: str, repo: str, tag: str) -> None:
        """Create or move a local repository tag pointing at *image_id*."""
        if not _IMAGE_OR_CONTAINER_ID.fullmatch(image_id):
            raise DockerRuntimeError("Docker image id is invalid")
        if (
            not repo.startswith("ghcr.io/pear-studio/")
            or not _SAFE_NAME.fullmatch(tag)
        ):
            raise DockerRuntimeError("Docker tag identity is invalid")
        await self.runtime._request(
            "POST",
            "/images/"
            + urllib.parse.quote(image_id, safe="")
            + "/tag?repo="
            + urllib.parse.quote(repo, safe="")
            + "&tag="
            + urllib.parse.quote(tag, safe=""),
            expected={201},
        )

    async def resolve_image_defaults(self, image_id: str) -> dict[str, Any]:
        if not _IMAGE_OR_CONTAINER_ID.fullmatch(image_id):
            raise DockerRuntimeError("Docker image id is invalid")
        payload = await self.runtime._request(
            "GET",
            "/images/" + urllib.parse.quote(image_id, safe="") + "/json",
            expected={200},
        )
        if not isinstance(payload, dict):
            raise DockerRuntimeError("Docker image inspect payload is invalid")
        return payload


def identity_from_payload(payload: dict[str, Any]) -> ContainerIdentity:
    """Extract exact identity facts; any ambiguity fails closed."""
    container_id = payload.get("Id")
    name = payload.get("Name")
    image_id = payload.get("Image")
    config = payload.get("Config")
    state = payload.get("State")
    host = payload.get("HostConfig")
    mounts = payload.get("Mounts")
    if (
        not isinstance(container_id, str)
        or not isinstance(name, str)
        or not name
        or not isinstance(image_id, str)
        or not _IMAGE_OR_CONTAINER_ID.fullmatch(image_id)
        or not isinstance(config, dict)
        or not isinstance(state, dict)
        or not isinstance(host, dict)
        or not isinstance(mounts, list)
        or any(not isinstance(mount, dict) for mount in mounts)
    ):
        raise DockerRuntimeError("Docker inspect payload is incomplete")
    labels = config.get("Labels")
    if labels is None:
        labels = {}
    if not isinstance(labels, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in labels.items()
    ):
        raise DockerRuntimeError("Docker container labels are invalid")
    restart = host.get("RestartPolicy")
    if restart is None:
        restart_policy: str | None = None
    elif isinstance(restart, dict) and isinstance(restart.get("Name"), str):
        restart_policy = restart["Name"]
    else:
        raise DockerRuntimeError("Docker restart policy is invalid")
    reference = config.get("Image")
    if not isinstance(reference, str) or not reference:
        raise DockerRuntimeError("Docker image reference is invalid")
    hostname = config.get("Hostname")
    if not isinstance(hostname, str) or not hostname:
        raise DockerRuntimeError("Docker container hostname is invalid")
    networks = payload.get("NetworkSettings")
    if not isinstance(networks, dict) or not isinstance(
        networks.get("Networks"), dict
    ):
        raise DockerRuntimeError("Docker network settings are invalid")
    return ContainerIdentity(
        container_id=container_id,
        name=name.removeprefix("/"),
        image_id=image_id,
        image_reference=reference,
        running=state.get("Running") is True,
        restart_policy=restart_policy,
        labels=labels,
        config=config,
        host_config=host,
        networks=networks["Networks"],
        hostname=hostname,
        mounts=tuple(mounts),
    )


def _clean_absolute_linux_path(
    value: object, *, label: str
) -> PurePosixPath:
    if not isinstance(value, (str, PurePosixPath)):
        raise DockerRuntimeError(f"Docker {label} is invalid")
    raw = str(value)
    if (
        not raw.startswith("/")
        or "\\" in raw
        or "\x00" in raw
        or any(ord(character) < 32 for character in raw)
    ):
        raise DockerRuntimeError(f"Docker {label} is not an absolute Linux path")
    components = raw.split("/")[1:]
    if any(component in {"", ".", ".."} for component in components):
        raise DockerRuntimeError(f"Docker {label} is not lexically canonical")
    path = PurePosixPath(raw)
    if str(path) != raw:
        raise DockerRuntimeError(f"Docker {label} is not lexically canonical")
    return path


def _parse_linux_bind(
    value: object,
) -> tuple[PurePosixPath, PurePosixPath, bool]:
    if not isinstance(value, str):
        raise DockerRuntimeError("Docker bind configuration is invalid")
    parts = value.split(":")
    if len(parts) not in {2, 3}:
        raise DockerRuntimeError("Docker bind configuration is ambiguous")
    source = _clean_absolute_linux_path(parts[0], label="bind source")
    destination = _clean_absolute_linux_path(parts[1], label="bind destination")
    modes = set(parts[2].split(",")) if len(parts) == 3 else set()
    if "" in modes or ({"ro", "rw"} <= modes):
        raise DockerRuntimeError("Docker bind mode is invalid")
    return source, destination, "ro" not in modes


__all__ = [
    "ContainerIdentity",
    "DockerHandoffExecutor",
    "identity_from_payload",
]
