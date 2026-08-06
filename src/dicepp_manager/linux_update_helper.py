"""One-shot Updater helper for the Linux Manager handoff transaction.

Started from the *source* Manager image with its own CLI entrypoint
(``python -m dicepp_manager.linux_update_helper``), this container holds no
ports, mounts the Docker socket and the transaction recovery directory
temporarily, and only switches the Manager containers:

1. verify the immutable request and the exact source Manager identity;
2. set handoff containers to ``restart=no``, stop and *rename* (never
   rebuild) the source Manager into its deterministic backup name;
3. create the target Manager under the official name with the verified
   target Image ID plus transaction labels and ``restart=no``;
4. wait for the first-write-wins ``decision``; any source restore is
   always preceded by a fresh decision re-read (commit is the only
   irreversible commit point), and a target that never became running
   by the startup deadline is restored early:
   - ``commit`` -> restore the target-side restart policy, delete the
     exact source backup container, write ``result=target-committed``;
   - ``rollback`` / deadline / target exit / startup never running ->
     restore the source Manager, write ``result=source-restored`` (or
     ``restore-failed``; an already recorded success is never
     overwritten).

``restore-source`` and ``finalize-committed`` modes are the documented
manual-recovery entry points for a host/Docker restart during the window:
they require the exact request identity and never invent decisions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .docker_handoff import (
    ContainerIdentity,
    DockerHandoffExecutor,
)
from .docker_runtime import (
    DockerRuntimeError,
    DockerSocketRuntimeAdapter,
)
from .linux_handoff import (
    DECISION_COMMIT,
    DECISION_ROLLBACK,
    RESULT_RESTORE_FAILED,
    RESULT_SOURCE_RESTORED,
    RESULT_TARGET_COMMITTED,
    _DECISION_FILENAME,
    _REQUEST_FILENAME,
    _RESULT_FILENAME,
    HandoffProtocolError,
    read_decision,
    read_request,
    read_result,
    write_result,
)

log = logging.getLogger("dicepp.linux_update_helper")

_POLL_INTERVAL_SECONDS = 2.0


class HelperError(RuntimeError):
    """Fatal helper failure; the failure site is preserved."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result_payload(
    request: Mapping[str, Any], value: str, *, error: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "format_version": request["format_version"],
        "transaction_id": request["transaction_id"],
        "operation_id": request["operation_id"],
        "value": value,
        "created_at": _now(),
    }
    if error is not None:
        payload["error"] = error
    return payload


def _deadline(request: Mapping[str, Any]) -> float:
    """Seconds left until the overall transaction deadline expires."""
    created = datetime.fromisoformat(request["created_at"])
    elapsed = (datetime.now(timezone.utc) - created).total_seconds()
    return float(request["transaction_deadline_seconds"]) - elapsed


def _startup_deadline(request: Mapping[str, Any]) -> float:
    """Seconds left until the target Manager startup/takeover deadline expires.

    The spec fixes two deadlines: the shorter startup/takeover deadline bounds
    the wait for the target Manager to take over or write a decision (a target
    that never became running by this deadline is treated as never having
    taken over), and the longer transaction deadline bounds the whole local
    transaction.  Either expiry triggers restoring the old Manager (unless a
    commit already exists).
    """
    created = datetime.fromisoformat(request["created_at"])
    elapsed = (datetime.now(timezone.utc) - created).total_seconds()
    return float(request["startup_deadline_seconds"]) - elapsed


def _manager_create_config(
    identity: ContainerIdentity, target_manager_image_id: str
) -> dict[str, Any]:
    """Only the image changes; labels/restart are handled by create()."""
    expected_hostname = identity.container_id[:12]
    if identity.hostname != expected_hostname:
        raise HelperError(
            "custom or ambiguous Manager hostname is unsupported by handoff v1"
        )
    config = dict(identity.config)
    # The source hostname is Docker's generated short container id.  It must
    # not be copied into the replacement: omitting it lets Docker generate a
    # hostname bound to the new target container id for the self-identity gate.
    config.pop("Hostname", None)
    config["Image"] = target_manager_image_id
    config["HostConfig"] = dict(identity.host_config)
    config["NetworkingConfig"] = {"EndpointsConfig": dict(identity.networks)}
    return config


def _tx_labels(request: Mapping[str, Any]) -> dict[str, str]:
    return {
        request["labels"]["transaction"]: request["transaction_id"],
        request["labels"]["role"]: "manager",
    }


async def _verify_source_manager(
    primitives: DockerHandoffExecutor, request: Mapping[str, Any]
) -> ContainerIdentity:
    """Require the exact source Manager identity captured in the request."""
    expected = request["manager"]
    found = await primitives.list_by_name(expected["name"])
    if found is None:
        raise HelperError(
            f"official Manager container {expected['name']} is missing"
        )
    identity = await primitives.inspect(found)
    if (
        identity.container_id != expected["container_id"]
        or identity.image_id != expected["image_id"]
        or identity.name != expected["name"]
    ):
        raise HelperError(
            "source Manager identity does not match the request; "
            "refusing to switch"
        )
    return identity


async def _verify_handoff_container(
    primitives: DockerHandoffExecutor,
    request: Mapping[str, Any],
    role: str,
    name: str,
) -> ContainerIdentity:
    """Locate a handoff container by name but require the exact captured identity.

    The request captures the precise container_id/image_id of the source
    Bot/Dashboard; the name is only a candidate lookup.  Anything occupying the
    name that is not the captured container must fail closed instead of being
    touched (restart=no or any other mutation).
    """
    captured = request[role]
    found = await primitives.list_by_name(name)
    if found is None:
        raise HelperError(f"managed {role} container {name} is missing")
    identity = await primitives.inspect(found)
    if (
        identity.container_id != captured["container_id"]
        or identity.image_id != captured["image_id"]
        or identity.name != name
        or identity.labels.get("com.docker.compose.project")
        != request["compose_project"]
    ):
        raise HelperError(
            f"{role} container identity does not match the request; "
            "refusing to switch"
        )
    return identity


async def _verify_target_container(
    primitives: DockerHandoffExecutor,
    request: Mapping[str, Any],
    container_id: str,
) -> ContainerIdentity:
    """New containers require transaction labels plus exact identity."""
    identity = await primitives.inspect(container_id)
    labels = _tx_labels(request)
    if identity.labels.get(request["labels"]["transaction"]) != request[
        "transaction_id"
    ] or identity.labels.get(request["labels"]["role"]) != "manager":
        raise HelperError(
            "target Manager is missing the transaction labels; refusing"
        )
    if identity.name != request["manager"]["name"]:
        raise HelperError("target Manager name does not match the request")
    if identity.image_id != request["target_manager_image_id"]:
        raise HelperError("target Manager image identity does not match the request")
    return identity


async def _set_handoff_restart_policies(
    primitives: DockerHandoffExecutor,
    request: Mapping[str, Any],
    identities: Mapping[str, ContainerIdentity],
) -> None:
    """Fail-closed restart=no for every handoff container before switching."""
    for role, identity in identities.items():
        if identity.restart_policy != "no":
            await primitives.set_restart_policy(identity.container_id, "no")
    for role in ("manager", "bot", "dashboard"):
        expected = request["restart_policies"][role]
        identity = identities[role]
        if identity.restart_policy not in (expected, "no"):
            raise HelperError(
                f"{role} restart policy changed unexpectedly during capture"
            )


async def _restore_source_manager(
    primitives: DockerHandoffExecutor,
    request: Mapping[str, Any],
    *,
    target_identity: ContainerIdentity | None,
) -> None:
    """Stop/delete the exact target, rename the source back, restore and start."""
    expected = request["manager"]
    if target_identity is not None:
        verified = await _verify_target_container(
            primitives, request, target_identity.container_id
        )
        await primitives.stop(verified.container_id)
        await primitives.delete(verified.container_id)
    backup_id = await primitives.list_by_name(expected["backup_name"])
    if backup_id is None:
        raise HelperError(
            f"source backup container {expected['backup_name']} is missing"
        )
    identity = await primitives.inspect(backup_id)
    if (
        identity.container_id != expected["container_id"]
        or identity.image_id != expected["image_id"]
        or identity.name != expected["backup_name"]
    ):
        raise HelperError("source backup identity does not match the request")
    await primitives.rename(identity.container_id, expected["name"])
    await primitives.set_restart_policy(
        identity.container_id, request["restart_policies"]["manager"]
    )
    await primitives.start(identity.container_id)


async def _restore_aliases(
    primitives: DockerHandoffExecutor, request: Mapping[str, Any]
) -> None:
    """Point both local current aliases back at the source Image IDs."""
    for alias in request["current_aliases"].values():
        await primitives.tag_image(alias["image_id"], *alias["name"].rsplit(":", 1))


async def _finalize_committed(
    primitives: DockerHandoffExecutor,
    request: Mapping[str, Any],
    *,
    source_backup_id: str | None,
) -> None:
    """Restore the target-side restart policy and delete the old source container."""
    expected = request["manager"]
    target_id = await primitives.list_by_name(expected["name"])
    if target_id is None:
        raise HelperError("target Manager container is missing at commit cleanup")
    await _verify_target_container(primitives, request, target_id)
    await primitives.set_restart_policy(
        target_id, request["restart_policies"]["manager"]
    )
    if source_backup_id is not None:
        identity = await primitives.inspect(source_backup_id)
        if (
            identity.container_id != expected["container_id"]
            or identity.image_id != expected["image_id"]
            or identity.name != expected["backup_name"]
        ):
            raise HelperError("source backup identity does not match the request")
        await primitives.delete(identity.container_id)


async def _write_failed_result(
    primitives: DockerHandoffExecutor,
    request: Mapping[str, Any],
    result_path: Path,
    *,
    error: Exception,
) -> None:
    identity: dict[str, str] | None = None
    try:
        source = await primitives.inspect(request["manager"]["container_id"])
        identity = {
            "container_id": source.container_id,
            "image_id": source.image_id,
            "summary": str(error)[-2000:],
        }
    except Exception:
        identity = {
            "container_id": request["manager"]["container_id"],
            "image_id": request["manager"]["image_id"],
            "summary": str(error)[-2000:],
        }
    try:
        existing = read_result(result_path, root=result_path.parent)
    except HandoffProtocolError:
        existing = None
    if existing is not None and existing["value"] in (
        RESULT_TARGET_COMMITTED,
        RESULT_SOURCE_RESTORED,
    ):
        # 成功终态是权威结果;失败现场只记录日志,不得把成功结果
        # 覆盖为 restore-failed(否则事务无法收敛)。
        log.error(
            "keep existing successful result %s; failure recorded without "
            "overwrite: %s",
            existing["value"],
            error,
        )
        return
    try:
        write_result(
            result_path,
            _result_payload(request, RESULT_RESTORE_FAILED, error=identity),
            root=result_path.parent,
        )
    except HandoffProtocolError as exc:
        log.error("failed to persist restore-failed result: %s", exc)


async def _complete_committed(
    primitives: DockerHandoffExecutor,
    request: Mapping[str, Any],
    tx_dir: Path,
    *,
    source_backup_id: str | None,
) -> int:
    """A durable commit exists: finish target-side cleanup, never roll back.

    ``decision=commit`` is the single irreversible commit point; once it
    exists no Updater may choose to restore the source.
    """
    result_path = tx_dir / _RESULT_FILENAME
    await _finalize_committed(
        primitives, request, source_backup_id=source_backup_id
    )
    write_result(
        result_path,
        _result_payload(request, RESULT_TARGET_COMMITTED),
        root=tx_dir,
    )
    return 0


async def _recover_target_loss(
    primitives: DockerHandoffExecutor,
    request: Mapping[str, Any],
    tx_dir: Path,
    *,
    error: Exception,
) -> int:
    """The target container vanished during the poll loop: fail closed.

    Re-read the decision first: a durable commit forbids any source rollback
    (the target side simply cannot be finalized).  Otherwise restore the
    source backup best-effort, then record the failure site with the actual
    container identity and an error summary, per the protocol contract.
    """
    decision_path = tx_dir / _DECISION_FILENAME
    result_path = tx_dir / _RESULT_FILENAME
    try:
        authoritative = await asyncio.to_thread(
            read_decision,
            decision_path,
            transaction_id=request["transaction_id"],
            operation_id=request["operation_id"],
            root=tx_dir,
        )
    except HandoffProtocolError as exc:
        log.error("cannot re-read decision after target loss: %s", exc)
        await _write_failed_result(primitives, request, result_path, error=error)
        return 1
    if authoritative == DECISION_COMMIT:
        log.error(
            "target container is gone but a commit decision exists; "
            "refusing to roll back the source"
        )
        await _write_failed_result(primitives, request, result_path, error=error)
        return 1
    try:
        await _restore_source_manager(primitives, request, target_identity=None)
    except Exception as restore_exc:
        log.error("source restore after target loss failed: %s", restore_exc)
    await _write_failed_result(primitives, request, result_path, error=error)
    return 1


async def run_switch(
    primitives: DockerHandoffExecutor,
    request: Mapping[str, Any],
    tx_dir: Path,
    *,
    poll_interval: float = _POLL_INTERVAL_SECONDS,
) -> int:
    """Run the normal switch transaction; returns a process exit code."""
    decision_path = tx_dir / _DECISION_FILENAME
    result_path = tx_dir / _RESULT_FILENAME
    created_id: str | None = None
    renamed = False
    try:
        source = await _verify_source_manager(primitives, request)
        backup_name = request["manager"]["backup_name"]
        backup_id = await primitives.list_by_name(backup_name)
        if backup_id is not None:
            raise HelperError("the transaction backup name is already in use")
        target_id = await primitives.list_by_name(request["manager"]["name"])
        if target_id is not None and target_id != source.container_id:
            raise HelperError("the official Manager name is occupied")
        # 名称只作为候选定位;必须与 request 捕获的精确 container_id /
        # image_id(以及 compose project)比对,不匹配一律 fail closed。
        identities = {
            "manager": source,
            "bot": await _verify_handoff_container(
                primitives, request, "bot", "dicepp"
            ),
            "dashboard": await _verify_handoff_container(
                primitives, request, "dashboard", "dicepp-dashboard"
            ),
        }
        target_config = _manager_create_config(
            source, request["target_manager_image_id"]
        )
        await _set_handoff_restart_policies(primitives, request, identities)
        # Stop the old Manager, keep it as the backup container (no rebuild,
        # no label backfill), then create the target under the official name.
        await primitives.stop(source.container_id)
        await primitives.rename(source.container_id, backup_name)
        renamed = True
        created_id = await primitives.create(
            request["manager"]["name"],
            target_config,
            extra_labels=_tx_labels(request),
            restart_policy="no",
        )
        await primitives.start(created_id)
    except Exception as exc:
        # Once the source Manager was renamed away, any switch failure must
        # first restore it before recording the failure site.  A partially
        # created target is removed only after exact identity verification.
        if renamed:
            target_identity = None
            if created_id is not None:
                try:
                    target_identity = await primitives.inspect(created_id)
                except DockerRuntimeError:
                    target_identity = None
            try:
                await _restore_source_manager(
                    primitives, request, target_identity=target_identity
                )
            except Exception as restore_exc:
                log.error(
                    "source restore after switch failure failed: %s", restore_exc
                )
        await _write_failed_result(primitives, request, result_path, error=exc)
        log.error("handoff switch failed: %s", exc)
        return 1

    transaction_deadline_seconds = _deadline(request)
    if transaction_deadline_seconds <= 0:
        log.error("transaction deadline already expired")
        try:
            # 恢复来源前重读 decision:commit 一旦 durable 即不可回退。
            authoritative = await asyncio.to_thread(
                read_decision,
                decision_path,
                transaction_id=request["transaction_id"],
                operation_id=request["operation_id"],
                root=tx_dir,
            )
            if authoritative == DECISION_COMMIT:
                return await _complete_committed(
                    primitives, request, tx_dir, source_backup_id=source.container_id
                )
        except Exception as exc:
            await _write_failed_result(primitives, request, result_path, error=exc)
            log.error("deadline-expired finish failed: %s", exc)
            return 1
        try:
            await _restore_source_manager(primitives, request, target_identity=None)
        except Exception as exc:
            await _write_failed_result(primitives, request, result_path, error=exc)
            return 1
        await _write_result_source_restored(primitives, request, result_path)
        return 0

    decision = None
    target_exited = False
    seen_running = False
    # 两个固定期限:startup 期限只约束"目标从未接管"(从未 running)的情形,
    # 到点且从未 running 即恢复来源;目标一旦 running 就只等 decision,直到
    # transaction 期限——目标 running 但尚未写 decision 是正常迁移,不得在
    # startup 期限恢复。
    transaction_deadline_seconds = _deadline(request)
    startup_deadline_seconds = _startup_deadline(request)
    while transaction_deadline_seconds > 0:
        decision = await asyncio.to_thread(
            read_decision,
            decision_path,
            transaction_id=request["transaction_id"],
            operation_id=request["operation_id"],
            root=tx_dir,
        )
        if decision is not None:
            break
        try:
            target = await primitives.inspect(created_id)
        except DockerRuntimeError as exc:
            # 目标容器不可见(被删除/重建):按契约记录失败现场,fail closed。
            return await _recover_target_loss(
                primitives, request, tx_dir, error=exc
            )
        if target.running:
            seen_running = True
        elif seen_running:
            target_exited = True
            break
        elif startup_deadline_seconds <= 0:
            # 从未 running 且 startup 期限已到:目标未接管 → 恢复来源。
            break
        await asyncio.sleep(poll_interval)
        transaction_deadline_seconds = _deadline(request)
        startup_deadline_seconds = _startup_deadline(request)

    try:
        if decision == DECISION_COMMIT:
            return await _complete_committed(
                primitives, request, tx_dir, source_backup_id=source.container_id
            )
        # 回退路径:rollback / 目标退出 / transaction 期限耗尽 / startup
        # 期限耗尽且目标从未 running。恢复来源前必须重读 decision:目标
        # Manager 可能在最后一次读之后落盘了 commit(随后退出或崩溃)——commit
        # 是唯一不可逆提交点,存在即不得回退。
        authoritative = await asyncio.to_thread(
            read_decision,
            decision_path,
            transaction_id=request["transaction_id"],
            operation_id=request["operation_id"],
            root=tx_dir,
        )
        if authoritative == DECISION_COMMIT:
            log.warning(
                "commit appeared after the last poll; finalizing the target "
                "side instead of rolling back"
            )
            return await _complete_committed(
                primitives, request, tx_dir, source_backup_id=source.container_id
            )
        target_identity = await primitives.inspect(created_id)
        await _restore_source_manager(
            primitives, request, target_identity=target_identity
        )
        write_result(
            result_path,
            _result_payload(request, RESULT_SOURCE_RESTORED),
            root=tx_dir,
        )
        return 0
    except Exception as exc:
        await _write_failed_result(primitives, request, result_path, error=exc)
        log.error("handoff finish failed: %s", exc)
        return 1


async def _write_result_source_restored(
    primitives: DockerHandoffExecutor,
    request: Mapping[str, Any],
    result_path: Path,
) -> None:
    write_result(
        result_path,
        _result_payload(request, RESULT_SOURCE_RESTORED),
        root=result_path.parent,
    )


async def run_restore_source(
    primitives: DockerHandoffExecutor,
    request: Mapping[str, Any],
    tx_dir: Path,
) -> int:
    """Manual recovery: no valid commit -> restore the source side."""
    decision_path = tx_dir / _DECISION_FILENAME
    result_path = tx_dir / _RESULT_FILENAME
    decision = await asyncio.to_thread(
        read_decision,
        decision_path,
        transaction_id=request["transaction_id"],
        operation_id=request["operation_id"],
        root=tx_dir,
    )
    if decision == DECISION_COMMIT:
        raise HelperError(
            "a commit decision exists; restore-source is forbidden, "
            "use finalize-committed"
        )
    try:
        await _restore_aliases(primitives, request)
        # Remove the exact target Manager, then restore the source backup.
        target = await primitives.list_by_name(request["manager"]["name"])
        target_identity = None
        if target is not None:
            target_identity = await _verify_target_container(
                primitives, request, target
            )
        await _restore_source_manager(
            primitives, request, target_identity=target_identity
        )
        write_result(
            result_path,
            _result_payload(request, RESULT_SOURCE_RESTORED),
            root=tx_dir,
        )
        return 0
    except Exception as exc:
        await _write_failed_result(primitives, request, result_path, error=exc)
        return 1


async def run_finalize_committed(
    primitives: DockerHandoffExecutor,
    request: Mapping[str, Any],
    tx_dir: Path,
) -> int:
    """Manual recovery: a valid commit exists -> finish target-side cleanup."""
    decision_path = tx_dir / _DECISION_FILENAME
    result_path = tx_dir / _RESULT_FILENAME
    decision = await asyncio.to_thread(
        read_decision,
        decision_path,
        transaction_id=request["transaction_id"],
        operation_id=request["operation_id"],
        root=tx_dir,
    )
    if decision != DECISION_COMMIT:
        raise HelperError(
            "no commit decision exists; finalize-committed requires a commit"
        )
    try:
        backup_id = await primitives.list_by_name(request["manager"]["backup_name"])
        await _finalize_committed(
            primitives, request, source_backup_id=backup_id
        )
        write_result(
            result_path,
            _result_payload(request, RESULT_TARGET_COMMITTED),
            root=tx_dir,
        )
        return 0
    except Exception as exc:
        await _write_failed_result(primitives, request, result_path, error=exc)
        return 1


async def _load_request(tx_dir: Path) -> dict[str, Any]:
    request_path = tx_dir / _REQUEST_FILENAME
    try:
        return read_request(request_path, root=tx_dir)
    except (HandoffProtocolError, OSError) as exc:
        raise HelperError(f"cannot load the immutable request: {exc}") from exc


async def _run(
    *,
    tx_dir: Path,
    socket_path: str,
    mode: str,
    poll_interval: float,
) -> int:
    request = await _load_request(tx_dir)
    adapter = DockerSocketRuntimeAdapter(
        socket_path=socket_path,
        allowed_runtime_units={"manager-helper"},
        timeout=30.0,
    )
    primitives = DockerHandoffExecutor(adapter)
    if mode == "switch":
        return await run_switch(
            primitives, request, tx_dir, poll_interval=poll_interval
        )
    if mode == "restore-source":
        return await run_restore_source(primitives, request, tx_dir)
    if mode == "finalize-committed":
        return await run_finalize_committed(primitives, request, tx_dir)
    raise HelperError(f"unknown helper mode: {mode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dicepp-linux-update-helper",
        description="One-shot Updater for the Linux Manager handoff transaction",
    )
    parser.add_argument(
        "--transaction-dir",
        required=True,
        type=Path,
        help="mounted transaction recovery directory containing the request",
    )
    parser.add_argument(
        "--socket",
        default="/var/run/docker.sock",
        help="Docker Engine socket path",
    )
    parser.add_argument(
        "--mode",
        choices=("switch", "restore-source", "finalize-committed"),
        default="switch",
    )
    parser.add_argument("--poll-interval", type=float, default=_POLL_INTERVAL_SECONDS)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        return asyncio.run(
            _run(
                tx_dir=args.transaction_dir,
                socket_path=args.socket,
                mode=args.mode,
                poll_interval=args.poll_interval,
            )
        )
    except HelperError as exc:
        log.error("%s", exc)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        log.exception("unexpected helper failure: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
