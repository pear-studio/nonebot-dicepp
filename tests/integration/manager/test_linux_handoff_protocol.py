"""Linux Manager handoff 协议层：request/decision/result 契约测试。

保护的行为契约：
- request 由来源 Manager 唯一写入，之后不可变（同值幂等、异值拒绝）；
- decision 是 first-write-wins 的一次性提交点，已存在时只能校验并按原值
  继续，值不同或文件异常必须 fail closed，绝不改写；
- result 由 Updater 角色写入，只接受已知枚举；
- 所有文件路径必须限制在受信任事务目录内，拒绝 symlink/reparse；
- 写入是 durable atomic（临时文件 + fsync + no-replace 发布），不残留
  半写文件。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dicepp_manager.linux_handoff import (
    DECISION_COMMIT,
    DECISION_ROLLBACK,
    LINUX_MANAGER_HANDOFF_FORMAT,
    RESULT_RESTORE_FAILED,
    RESULT_SOURCE_RESTORED,
    RESULT_TARGET_COMMITTED,
    HandoffProtocolError,
    read_decision,
    read_request,
    read_result,
    write_decision,
    write_request,
    write_result,
)
from tests.support.fs_utils import symlink_or_skip


from tests.support.handoff_fixtures import (
    decision_payload,
    request_payload,
    result_payload,
)


def _tx_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "manager" / "recovery" / ("a" * 32)
    directory.mkdir(parents=True)
    return directory


class TestRequest:
    def test_request_roundtrip_preserves_payload(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.request.json"
        payload = request_payload()

        write_request(path, payload)

        assert read_request(path) == payload

    def test_request_rewrite_same_value_is_idempotent(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.request.json"
        payload = request_payload()

        write_request(path, payload)
        write_request(path, payload)  # 不抛异常

        assert read_request(path) == payload

    def test_request_rewrite_different_value_is_rejected(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.request.json"
        original = request_payload()
        write_request(path, original)

        with pytest.raises(HandoffProtocolError, match="immutable"):
            write_request(path, request_payload(target_version="3.0.0rc22"))

        assert read_request(path) == original

    def test_request_rejects_missing_required_fields(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.request.json"
        payload = request_payload()
        del payload["manager"]  # type: ignore[misc]

        with pytest.raises(HandoffProtocolError, match="manager"):
            write_request(path, payload)

    def test_request_rejects_invalid_container_identity(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.request.json"

        with pytest.raises(HandoffProtocolError, match="container_id"):
            write_request(path, request_payload(manager={"name": "dicepp-manager"}))

    def test_request_rejects_version_tag_as_mutable_current_alias(
        self, tmp_path: Path
    ) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.request.json"
        payload = request_payload()
        payload["current_aliases"]["bot"]["name"] = (
            "ghcr.io/pear-studio/nonebot-dicepp:v3.0.0rc20"
        )

        with pytest.raises(HandoffProtocolError, match="dicepp-current"):
            write_request(path, payload)

    def test_request_rejects_absolute_snapshot_path(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.request.json"

        with pytest.raises(HandoffProtocolError, match="dashboard_db"):
            write_request(
                path,
                request_payload(
                    dashboard_db={
                        "path": "/etc/dashboard.db",
                        "sha256": "0" * 64,
                    }
                ),
            )

    def test_request_rejects_symlinked_transaction_directory(
        self, tmp_path: Path
    ) -> None:
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "linked"
        symlink_or_skip(link, real, target_is_directory=True)
        path = link / "linux-manager-switch.request.json"

        with pytest.raises(HandoffProtocolError, match="unsafe"):
            write_request(path, request_payload())


class TestDecision:
    def test_first_write_wins_keeps_commit_over_later_rollback(
        self, tmp_path: Path
    ) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.decision.json"

        assert write_decision(path, decision_payload(DECISION_COMMIT)) == DECISION_COMMIT
        with pytest.raises(HandoffProtocolError, match="first-write-wins|already exists"):
            write_decision(path, decision_payload(DECISION_ROLLBACK))

        assert read_decision(path) == DECISION_COMMIT
        body = json.loads(path.read_text(encoding="utf-8"))
        assert body["value"] == DECISION_COMMIT

    def test_existing_same_value_retry_succeeds(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.decision.json"
        write_decision(path, decision_payload(DECISION_COMMIT))

        assert write_decision(path, decision_payload(DECISION_COMMIT)) == DECISION_COMMIT

    def test_conflicting_existing_value_fails_closed(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.decision.json"
        write_decision(path, decision_payload(DECISION_ROLLBACK))

        with pytest.raises(HandoffProtocolError):
            write_decision(path, decision_payload(DECISION_COMMIT))

        assert read_decision(path) == DECISION_ROLLBACK

    def test_write_after_crashed_publish_recovers(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.decision.json"
        orphan = path.with_name(f".{path.name}.{'0' * 32}.tmp")
        orphan.write_text("partial", encoding="utf-8")

        assert write_decision(path, decision_payload(DECISION_COMMIT)) == DECISION_COMMIT
        assert read_decision(path) == DECISION_COMMIT

    def test_read_missing_returns_none(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.decision.json"

        assert read_decision(path) is None

    def test_read_corrupt_file_fails_closed(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.decision.json"
        path.write_text("{not json", encoding="utf-8")

        with pytest.raises(HandoffProtocolError):
            read_decision(path)

    def test_read_foreign_transaction_is_rejected(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.decision.json"
        write_decision(path, decision_payload(DECISION_COMMIT))

        with pytest.raises(HandoffProtocolError, match="transaction"):
            read_decision(path, transaction_id="f" * 32)

    def test_read_foreign_operation_is_rejected(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.decision.json"
        write_decision(path, decision_payload(DECISION_COMMIT))

        with pytest.raises(HandoffProtocolError, match="operation"):
            read_decision(path, operation_id="e" * 32)

    def test_unsupported_format_version_is_rejected(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.decision.json"

        with pytest.raises(HandoffProtocolError, match="format"):
            write_decision(path, decision_payload(DECISION_COMMIT, format_version=99))

    def test_unknown_value_is_rejected_on_write(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.decision.json"

        with pytest.raises(HandoffProtocolError, match="value"):
            write_decision(path, decision_payload("sideways"))


class TestResult:
    def test_result_roundtrip(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.result.json"
        payload = result_payload()

        write_result(path, payload)

        assert read_result(path) == payload

    def test_result_unknown_value_is_rejected(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.result.json"

        with pytest.raises(HandoffProtocolError, match="value"):
            write_result(path, result_payload("half-done"))

    def test_restore_failed_records_error_summary(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.result.json"
        payload = result_payload(
            RESULT_RESTORE_FAILED,
            error={
                "container_id": "c" * 64,
                "image_id": "sha256:" + "d" * 64,
                "summary": "container start failed",
            },
        )

        write_result(path, payload)

        assert read_result(path)["error"]["summary"] == "container start failed"

    def test_result_error_rejects_unknown_fields(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.result.json"
        payload = result_payload(RESULT_RESTORE_FAILED, error={"token": "secret"})

        with pytest.raises(HandoffProtocolError, match="restore-failed"):
            write_result(path, payload)


class TestDurability:
    def test_writes_leave_no_temporary_files(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        write_request(tx_dir / "linux-manager-switch.request.json", request_payload())
        write_decision(
            tx_dir / "linux-manager-switch.decision.json",
            decision_payload(DECISION_COMMIT),
        )
        write_result(tx_dir / "linux-manager-switch.result.json", result_payload())

        leftovers = [
            child
            for child in tx_dir.iterdir()
            if child.name.endswith(".tmp") or child.name.startswith(".")
        ]
        assert leftovers == []


class TestReviewGaps:
    """Review workflow findings: race branch, corrupt-write, symlink and root."""

    def test_decision_lost_race_returns_existing_value(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """落败方在 no-replace 发布后读回既有同值 decision 并返回权威值。"""
        import dicepp_manager.linux_handoff as module

        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.decision.json"
        winner = decision_payload(DECISION_COMMIT)
        module.write_decision(path, winner)

        real_link = module.os.link

        def pretend_lost_race(temporary, final, *args, **kwargs):
            raise FileExistsError("another writer published first")

        monkeypatch.setattr(module.os, "link", pretend_lost_race)
        assert module.write_decision(path, winner) == DECISION_COMMIT
        monkeypatch.setattr(module.os, "link", real_link)
        body = json.loads(path.read_text(encoding="utf-8"))
        assert body["value"] == DECISION_COMMIT

    def test_decision_lost_race_conflicting_value_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """落败方发现既有异值 decision 时必须 fail closed 且不得改写文件。"""
        import dicepp_manager.linux_handoff as module

        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.decision.json"
        module.write_decision(path, decision_payload(DECISION_COMMIT))
        original = path.read_bytes()

        def pretend_lost_race(temporary, final, *args, **kwargs):
            raise FileExistsError("another writer published first")

        monkeypatch.setattr(module.os, "link", pretend_lost_race)
        with pytest.raises(HandoffProtocolError, match="different value"):
            module.write_decision(path, decision_payload(DECISION_ROLLBACK))
        assert path.read_bytes() == original

    def test_write_decision_rejects_corrupt_existing_file(
        self, tmp_path: Path
    ) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.decision.json"
        path.write_text("{broken", encoding="utf-8")

        with pytest.raises(HandoffProtocolError):
            write_decision(path, decision_payload(DECISION_COMMIT))
        assert path.read_text(encoding="utf-8") == "{broken"

    def test_write_request_rejects_corrupt_existing_file(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.request.json"
        path.write_text("{broken", encoding="utf-8")

        with pytest.raises(HandoffProtocolError):
            write_request(path, request_payload())
        assert path.read_text(encoding="utf-8") == "{broken"

    def test_write_result_rejects_corrupt_existing_file(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.result.json"
        path.write_text("{broken", encoding="utf-8")

        with pytest.raises(HandoffProtocolError):
            write_result(path, result_payload())
        assert path.read_text(encoding="utf-8") == "{broken"

    def test_read_corrupt_request_fails_closed(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.request.json"
        path.write_text("{broken", encoding="utf-8")

        with pytest.raises(HandoffProtocolError):
            read_request(path)

    def test_read_corrupt_result_fails_closed(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        path = tx_dir / "linux-manager-switch.result.json"
        path.write_text("{broken", encoding="utf-8")

        with pytest.raises(HandoffProtocolError):
            read_result(path)

    def test_decision_file_symlink_is_rejected(self, tmp_path: Path) -> None:
        tx_dir = _tx_dir(tmp_path)
        outside = tmp_path / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        path = tx_dir / "linux-manager-switch.decision.json"
        symlink_or_skip(path, outside)

        with pytest.raises(HandoffProtocolError):
            read_decision(path)
        with pytest.raises(HandoffProtocolError):
            write_decision(path, decision_payload(DECISION_COMMIT))

    def test_ancestor_symlink_is_rejected_with_root(
        self, tmp_path: Path
    ) -> None:
        """提供可信 root 时,祖先组件 symlink 必须拒绝,防止整体重定向。"""
        instance = tmp_path / "instance"
        recovery = instance / "manager" / "recovery"
        recovery.mkdir(parents=True)
        tx_dir = recovery / ("a" * 32)
        tx_dir.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        # 中间组件 manager/recovery 换成 symlink 指向 outside
        import shutil

        shutil.rmtree(recovery)
        symlink_or_skip(recovery, outside, target_is_directory=True)

        path = recovery / ("a" * 32) / "linux-manager-switch.decision.json"
        with pytest.raises(HandoffProtocolError, match="unsafe"):
            write_decision(path, decision_payload(DECISION_COMMIT), root=recovery)
        assert not (outside / ("a" * 32) / "linux-manager-switch.decision.json").exists()
