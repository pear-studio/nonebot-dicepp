"""Linux Updater helper 流程测试:switch / restore-source / finalize-committed。

保护的行为契约：
- switch:来源 Manager 精确身份验证、restart=no 切换、rename 保留来源容器、
  目标容器带事务标签创建、commit 后仅完成目标侧清理、rollback/期限/目标
  退出时恢复来源、失败现场保留(result=restore-failed 带错误摘要)；
- 来源恢复必须核对权威 result 与精确身份,不凭"未提交 journal"推断；
- restore-source 遇到 commit 必须拒绝;finalize-committed 无 commit 必须拒绝。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from dicepp_manager.docker_handoff import (
    ContainerIdentity,
    DockerHandoffExecutor,
)
from dicepp_manager.docker_runtime import DockerRuntimeError
from dicepp_manager import linux_update_helper
from dicepp_manager.linux_handoff import (
    DECISION_COMMIT,
    DECISION_ROLLBACK,
    RESULT_RESTORE_FAILED,
    RESULT_SOURCE_RESTORED,
    RESULT_TARGET_COMMITTED,
    write_decision,
    write_request,
    write_result,
)
from dicepp_manager.linux_update_helper import (
    HelperError,
    _finalize_committed,
    main,
    run_finalize_committed,
    run_restore_source,
    run_switch,
)
from tests.support.handoff_fixtures import (
    decision_payload,
    request_payload,
    result_payload,
    tx_dir,
)

MANAGER_OLD_IMG = "sha256:" + ("d" * 64)
MANAGER_NEW_IMG = "sha256:" + ("e" * 64)


class FakeContainer:
    def __init__(
        self,
        name: str,
        image_id: str,
        *,
        cid: str,
        running: bool = True,
        restart: str = "unless-stopped",
        labels: dict[str, str] | None = None,
        hostname: str | None = None,
    ) -> None:
        self.name = name
        self.image_id = image_id
        self.cid = cid
        self.running = running
        self.restart = restart
        self.hostname = hostname or cid[:12]
        self.labels = {
            "com.docker.compose.project": "dicepp",
            "com.docker.compose.service": name.removeprefix("dicepp-"),
            **(labels or {}),
        }

    def identity(self) -> ContainerIdentity:
        return ContainerIdentity(
            container_id=self.cid,
            name=self.name,
            image_id=self.image_id,
            image_reference="ghcr.io/pear-studio/dicepp-dashboard:test",
            running=self.running,
            restart_policy=self.restart,
            labels=self.labels,
            config={
                "Image": "ghcr.io/pear-studio/dicepp-dashboard:test",
                "Hostname": self.hostname,
                "Cmd": ["python", "-m", "dicepp_manager"],
                "Labels": dict(self.labels),
            },
            host_config={
                "Binds": ["/var/run/docker.sock:/var/run/docker.sock"],
                "RestartPolicy": {"Name": self.restart},
            },
            networks={},
            hostname=self.hostname,
        )


class FakePrimitives:
    """In-memory Docker state machine mirroring the Engine API semantics."""

    def __init__(self, *containers: FakeContainer) -> None:
        self.by_id: dict[str, FakeContainer] = {}
        self.by_name: dict[str, FakeContainer] = {}
        for container in containers:
            self.by_id[container.cid] = container
            self.by_name[container.name] = container
        self.ops: list[tuple[str, str]] = []
        self.created_configs: list[dict] = []
        self.fail_create: Exception | None = None

    async def inspect(self, container_id: str) -> ContainerIdentity:
        self.ops.append(("inspect", container_id))
        if container_id not in self.by_id:
            raise RuntimeError(f"container {container_id} not found")
        return self.by_id[container_id].identity()

    async def list_by_name(self, name: str) -> str | None:
        container = self.by_name.get(name)
        return container.cid if container is not None else None

    async def list_by_label(self, key: str, value: str) -> list[str]:
        return [
            container.cid
            for container in self.by_id.values()
            if container.labels.get(key) == value
        ]

    async def stop(self, container_id: str) -> None:
        self.ops.append(("stop", container_id))
        self.by_id[container_id].running = False

    async def start(self, container_id: str) -> None:
        self.ops.append(("start", container_id))
        self.by_id[container_id].running = True

    async def delete(self, container_id: str) -> None:
        self.ops.append(("delete", container_id))
        container = self.by_id.pop(container_id)
        self.by_name.pop(container.name, None)

    async def rename(self, container_id: str, new_name: str) -> None:
        self.ops.append(("rename", container_id))
        container = self.by_id[container_id]
        self.by_name.pop(container.name, None)
        container.name = new_name
        self.by_name[new_name] = container

    async def create(
        self,
        name: str,
        config,
        *,
        extra_labels: dict[str, str],
        restart_policy: str,
    ) -> str:
        self.ops.append(("create", name))
        self.created_configs.append(dict(config))
        if self.fail_create is not None:
            error, self.fail_create = self.fail_create, None
            raise error
        cid = "new-" + name
        labels = dict(config.get("Labels") or {})
        labels.update(extra_labels)
        container = FakeContainer(
            name,
            config["Image"],
            cid=cid,
            running=False,
            restart=restart_policy,
            labels=labels,
        )
        self.by_id[cid] = container
        self.by_name[name] = container
        return cid

    async def set_restart_policy(self, container_id: str, policy: str) -> None:
        self.ops.append(("update", container_id))
        self.by_id[container_id].restart = policy

    async def tag_image(self, image_id: str, repo: str, tag: str) -> None:
        self.ops.append(("tag", f"{repo}:{tag}={image_id}"))


@pytest.fixture
def environment(tmp_path: Path):
    directory = tx_dir(tmp_path)
    source = FakeContainer(
        "dicepp-manager", MANAGER_OLD_IMG, cid="c" * 64
    )
    bot = FakeContainer("dicepp", "sha256:" + "10" * 32, cid="b" * 64)
    dashboard = FakeContainer(
        "dicepp-dashboard", "sha256:" + "30" * 32, cid="d" * 64
    )
    # request 捕获的 Bot/Dashboard 身份必须与容器实际一致(helper 只认
    # 精确 container_id/image_id,不认名称)。
    request = request_payload(
        bot={"container_id": bot.cid, "image_id": bot.image_id},
        dashboard={"container_id": dashboard.cid, "image_id": dashboard.image_id},
    )
    write_request(directory / "linux-manager-switch.request.json", request)
    primitives = FakePrimitives(source, bot, dashboard)
    return {
        "tx_dir": directory,
        "request": request,
        "source": source,
        "bot": bot,
        "dashboard": dashboard,
        "primitives": primitives,
    }


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestSwitchCommit:
    async def test_commit_finalizes_target_side(self, environment) -> None:
        tx = environment["tx_dir"]
        primitives = environment["primitives"]
        write_decision(
            tx / "linux-manager-switch.decision.json",
            decision_payload(DECISION_COMMIT),
        )

        code = await run_switch(primitives, environment["request"], tx)

        assert code == 0
        result = _read_json(tx / "linux-manager-switch.result.json")
        assert result["value"] == RESULT_TARGET_COMMITTED
        # 旧来源容器已删除,目标容器保留
        assert primitives.by_name.get("dicepp-manager") is not None
        assert primitives.by_name["dicepp-manager"].cid == "new-dicepp-manager"
        assert primitives.by_name["dicepp-manager"].image_id == MANAGER_NEW_IMG
        assert "c" * 64 not in primitives.by_id
        target = primitives.by_name["dicepp-manager"]
        assert target.restart == "unless-stopped"
        assert target.labels["io.dicepp.upgrade-transaction"] == "a" * 32
        assert target.labels["io.dicepp.upgrade-role"] == "manager"
        assert "Hostname" not in primitives.created_configs[0]
        # 交接窗口内所有容器 restart=no
        assert primitives.by_name["dicepp"].restart == "no"
        assert primitives.by_name["dicepp-dashboard"].restart == "no"

    async def test_custom_source_hostname_is_rejected_before_switch(
        self, environment
    ) -> None:
        tx = environment["tx_dir"]
        source = environment["source"]
        primitives = environment["primitives"]
        source.hostname = "custom-manager-host"

        code = await run_switch(primitives, environment["request"], tx)

        assert code == 1
        assert primitives.by_name["dicepp-manager"] is source
        assert primitives.created_configs == []
        assert not any(
            operation in {"stop", "rename", "create"}
            for operation, _value in primitives.ops
        )
        result = _read_json(tx / "linux-manager-switch.result.json")
        assert result["value"] == RESULT_RESTORE_FAILED

    async def test_rollback_restores_source(self, environment) -> None:
        tx = environment["tx_dir"]
        primitives = environment["primitives"]
        write_decision(
            tx / "linux-manager-switch.decision.json",
            decision_payload(DECISION_ROLLBACK),
        )

        code = await run_switch(primitives, environment["request"], tx)

        assert code == 0
        result = _read_json(tx / "linux-manager-switch.result.json")
        assert result["value"] == RESULT_SOURCE_RESTORED
        manager = primitives.by_name["dicepp-manager"]
        assert manager.cid == "c" * 64
        assert manager.image_id == MANAGER_OLD_IMG
        assert manager.running is True
        assert manager.restart == "unless-stopped"
        assert "new-dicepp-manager" not in primitives.by_id

    async def test_target_exit_restores_source(self, environment) -> None:
        """目标曾 running 随后崩溃退出:立即恢复来源,不等到 startup 期限。"""
        tx = environment["tx_dir"]
        primitives = environment["primitives"]
        original_inspect = primitives.inspect

        async def crash_after_first_running_observation(container_id: str):
            identity = await original_inspect(container_id)
            if container_id == "new-dicepp-manager" and identity.running:
                # 第一次观察到 running 后目标立即崩溃退出
                primitives.by_id[container_id].running = False
            return identity

        primitives.inspect = crash_after_first_running_observation
        code = await run_switch(
            primitives, environment["request"], tx, poll_interval=0.01
        )

        assert code == 0
        result = _read_json(tx / "linux-manager-switch.result.json")
        assert result["value"] == RESULT_SOURCE_RESTORED
        manager = primitives.by_name["dicepp-manager"]
        assert manager.cid == "c" * 64 and manager.running is True

    async def test_deadline_expiry_restores_source(self, tmp_path: Path) -> None:
        tx = tx_dir(tmp_path)
        # created_at 已是过去很久 → 期限立即耗尽
        source = FakeContainer("dicepp-manager", MANAGER_OLD_IMG, cid="c" * 64)
        bot = FakeContainer("dicepp", "sha256:" + "10" * 32, cid="b" * 64)
        dashboard = FakeContainer(
            "dicepp-dashboard", "sha256:" + "30" * 32, cid="d" * 64
        )
        request = request_payload(
            created_at="2020-01-01T00:00:00+00:00",
            transaction_deadline_seconds=60,
            bot={"container_id": bot.cid, "image_id": bot.image_id},
            dashboard={
                "container_id": dashboard.cid,
                "image_id": dashboard.image_id,
            },
        )
        write_request(tx / "linux-manager-switch.request.json", request)
        primitives = FakePrimitives(source, bot, dashboard)

        code = await run_switch(
            primitives, request, tx, poll_interval=0.01
        )

        assert code == 0
        result = _read_json(tx / "linux-manager-switch.result.json")
        assert result["value"] == RESULT_SOURCE_RESTORED
        assert primitives.by_name["dicepp-manager"].cid == "c" * 64

    async def test_source_identity_mismatch_fails_closed(self, environment) -> None:
        tx = environment["tx_dir"]
        primitives = environment["primitives"]
        request = request_payload(manager={
            "container_id": "9" * 64,
            "name": "dicepp-manager",
            "backup_name": "dicepp-manager.aaaaaaaa",
            "image_id": MANAGER_OLD_IMG,
        })

        code = await run_switch(primitives, request, tx)

        assert code == 1
        result = _read_json(tx / "linux-manager-switch.result.json")
        assert result["value"] == RESULT_RESTORE_FAILED
        assert result["error"]["container_id"] == "9" * 64
        # 未做任何切换
        assert primitives.by_name["dicepp-manager"].cid == "c" * 64

    async def test_create_failure_after_rename_restores_source(
        self, environment
    ) -> None:
        tx = environment["tx_dir"]
        primitives = environment["primitives"]
        primitives.fail_create = RuntimeError("engine create failed")

        code = await run_switch(primitives, environment["request"], tx)

        assert code == 1
        result = _read_json(tx / "linux-manager-switch.result.json")
        assert result["value"] == RESULT_RESTORE_FAILED
        # 来源已恢复
        manager = primitives.by_name["dicepp-manager"]
        assert manager.cid == "c" * 64
        assert manager.running is True
        assert "new-dicepp-manager" not in primitives.by_id

    async def test_commit_after_last_poll_forbids_source_rollback(
        self, environment, monkeypatch
    ) -> None:
        """R2: 轮询最后一次读到 None 之后目标才落盘 commit → 恢复来源前的
        重读必须走 commit 收敛,不得恢复来源。"""
        tx = environment["tx_dir"]
        primitives = environment["primitives"]
        original_inspect = primitives.inspect

        async def crash_after_first_running_observation(container_id: str):
            identity = await original_inspect(container_id)
            if container_id == "new-dicepp-manager" and identity.running:
                # 目标曾 running,随后退出:第二次轮询观察到退出,循环 break
                primitives.by_id[container_id].running = False
            return identity

        primitives.inspect = crash_after_first_running_observation
        # 轮询的两次读取都返回 None(commit 尚未落盘);回退分支重读时才
        # 返回 commit —— 模拟"最后一次读 None 之后 target 才提交并退出"。
        calls = {"n": 0}
        real_read_decision = linux_update_helper.read_decision

        def commit_after_two_reads(path, **kwargs):
            calls["n"] += 1
            if calls["n"] <= 2:
                return real_read_decision(path, **kwargs)
            return DECISION_COMMIT

        monkeypatch.setattr(
            linux_update_helper, "read_decision", commit_after_two_reads
        )
        code = await run_switch(
            primitives, environment["request"], tx, poll_interval=0.01
        )

        assert code == 0
        result = _read_json(tx / "linux-manager-switch.result.json")
        assert result["value"] == RESULT_TARGET_COMMITTED
        # 来源未被恢复:目标保留在正式名,来源备份已删除
        assert primitives.by_name["dicepp-manager"].cid == "new-dicepp-manager"
        assert "c" * 64 not in primitives.by_id

    @pytest.mark.parametrize(
        ("name", "image_id"),
        [
            ("dicepp", "sha256:" + "10" * 32),
            ("dicepp-dashboard", "sha256:" + "30" * 32),
        ],
    )
    async def test_managed_container_identity_mismatch_fails_closed(
        self, environment, name, image_id
    ) -> None:
        """R5: 名称被非捕获容器占用(container_id 与 request 不符)→ fail closed。"""
        tx = environment["tx_dir"]
        primitives = environment["primitives"]
        # 同名重建的容器:cid 与 request 捕获值不一致
        imposter = FakeContainer(name, image_id, cid="9" * 64)
        primitives.by_name[name] = imposter
        primitives.by_id["9" * 64] = imposter

        code = await run_switch(primitives, environment["request"], tx)

        assert code == 1
        result = _read_json(tx / "linux-manager-switch.result.json")
        assert result["value"] == RESULT_RESTORE_FAILED
        assert "does not match" in result["error"]["summary"]
        # 来源 Manager 未被停止/改名,非捕获容器未被翻转 restart policy
        manager = primitives.by_name["dicepp-manager"]
        assert manager.cid == "c" * 64 and manager.running is True
        assert primitives.by_name[name].restart == "unless-stopped"
        assert all(op[0] != "update" for op in primitives.ops)

    async def test_startup_deadline_never_running_restores_source(
        self, tmp_path: Path
    ) -> None:
        """R6: 目标 start 后从未 running 且无 decision → startup 期限后恢复来源。"""
        tx = tx_dir(tmp_path)
        source = FakeContainer("dicepp-manager", MANAGER_OLD_IMG, cid="c" * 64)
        bot = FakeContainer("dicepp", "sha256:" + "10" * 32, cid="b" * 64)
        dashboard = FakeContainer(
            "dicepp-dashboard", "sha256:" + "30" * 32, cid="d" * 64
        )
        request = request_payload(
            startup_deadline_seconds=1,
            bot={"container_id": bot.cid, "image_id": bot.image_id},
            dashboard={
                "container_id": dashboard.cid,
                "image_id": dashboard.image_id,
            },
        )
        write_request(tx / "linux-manager-switch.request.json", request)
        primitives = FakePrimitives(source, bot, dashboard)
        original_start = primitives.start

        async def exit_after_start(container_id: str) -> None:
            await original_start(container_id)
            if container_id == "new-dicepp-manager":
                primitives.by_id[container_id].running = False

        primitives.start = exit_after_start
        started_at = time.monotonic()
        code = await run_switch(primitives, request, tx, poll_interval=0.05)
        elapsed = time.monotonic() - started_at

        assert code == 0
        # 在 startup 期限(1s)之后才恢复,而非立即
        assert elapsed >= 0.8
        result = _read_json(tx / "linux-manager-switch.result.json")
        assert result["value"] == RESULT_SOURCE_RESTORED
        manager = primitives.by_name["dicepp-manager"]
        assert manager.cid == "c" * 64 and manager.running is True
        assert "new-dicepp-manager" not in primitives.by_id

    async def test_startup_deadline_ignored_while_target_running(
        self, tmp_path: Path
    ) -> None:
        """R6: 目标已 running 但未写 decision 是正常迁移——startup 期限不得
        恢复,继续等 decision 直到 transaction 期限。"""
        tx = tx_dir(tmp_path)
        source = FakeContainer("dicepp-manager", MANAGER_OLD_IMG, cid="c" * 64)
        bot = FakeContainer("dicepp", "sha256:" + "10" * 32, cid="b" * 64)
        dashboard = FakeContainer(
            "dicepp-dashboard", "sha256:" + "30" * 32, cid="d" * 64
        )
        request = request_payload(
            startup_deadline_seconds=1,
            transaction_deadline_seconds=2,
            bot={"container_id": bot.cid, "image_id": bot.image_id},
            dashboard={
                "container_id": dashboard.cid,
                "image_id": dashboard.image_id,
            },
        )
        write_request(tx / "linux-manager-switch.request.json", request)
        primitives = FakePrimitives(source, bot, dashboard)
        started_at = time.monotonic()
        code = await run_switch(primitives, request, tx, poll_interval=0.05)
        elapsed = time.monotonic() - started_at

        assert code == 0
        # 未在 startup 期限(1s)恢复;等到 transaction 期限(2s)才恢复
        assert elapsed >= 1.6
        result = _read_json(tx / "linux-manager-switch.result.json")
        assert result["value"] == RESULT_SOURCE_RESTORED
        assert primitives.by_name["dicepp-manager"].cid == "c" * 64

    async def test_target_vanished_during_poll_records_failure(
        self, environment
    ) -> None:
        """轮询期间目标容器不可见:记录 restore-failed 并尽力恢复来源。"""
        tx = environment["tx_dir"]
        primitives = environment["primitives"]
        original_inspect = primitives.inspect

        async def inspect(container_id: str):
            if container_id == "new-dicepp-manager":
                raise DockerRuntimeError("target container was removed")
            return await original_inspect(container_id)

        primitives.inspect = inspect

        code = await run_switch(
            primitives, environment["request"], tx, poll_interval=0.01
        )

        assert code == 1
        result = _read_json(tx / "linux-manager-switch.result.json")
        assert result["value"] == RESULT_RESTORE_FAILED
        assert result["error"]["summary"]
        # 来源已尽力恢复
        manager = primitives.by_name["dicepp-manager"]
        assert manager.cid == "c" * 64 and manager.running is True

    async def test_target_vanished_after_commit_never_rolls_back(
        self, environment, monkeypatch
    ) -> None:
        """目标丢失但 commit 已 durable:不恢复来源,只记录失败现场。"""
        tx = environment["tx_dir"]
        primitives = environment["primitives"]
        original_inspect = primitives.inspect

        async def inspect(container_id: str):
            if container_id == "new-dicepp-manager":
                raise DockerRuntimeError("target container was removed")
            return await original_inspect(container_id)

        primitives.inspect = inspect
        # 轮询第一次读到 None,目标丢失后的重读返回 commit
        calls = {"n": 0}

        def commit_after_first_read(path, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return DECISION_COMMIT

        monkeypatch.setattr(
            linux_update_helper, "read_decision", commit_after_first_read
        )
        code = await run_switch(
            primitives, environment["request"], tx, poll_interval=0.01
        )

        assert code == 1
        result = _read_json(tx / "linux-manager-switch.result.json")
        assert result["value"] == RESULT_RESTORE_FAILED
        # 来源保持备份名,未被恢复回正式名
        assert primitives.by_name["dicepp-manager"].cid == "new-dicepp-manager"
        assert primitives.by_id["c" * 64].name == "dicepp-manager.aaaaaaaa"


class TestManualRecovery:
    async def test_restore_source_forbidden_after_commit(self, environment) -> None:
        tx = environment["tx_dir"]
        primitives = environment["primitives"]
        write_decision(
            tx / "linux-manager-switch.decision.json",
            decision_payload(DECISION_COMMIT),
        )

        with pytest.raises(HelperError, match="commit"):
            await run_restore_source(primitives, environment["request"], tx)

    async def test_restore_source_restores_aliases_and_manager(
        self, environment
    ) -> None:
        tx = environment["tx_dir"]
        primitives = environment["primitives"]
        # 模拟:来源已改名,目标已接管
        source = primitives.by_id["c" * 64]
        await primitives.rename(source.cid, "dicepp-manager.aaaaaaaa")
        await primitives.create(
            "dicepp-manager",
            {"Image": MANAGER_NEW_IMG, "Labels": {}, "HostConfig": {}},
            extra_labels={
                "io.dicepp.upgrade-transaction": "a" * 32,
                "io.dicepp.upgrade-role": "manager",
            },
            restart_policy="no",
        )

        code = await run_restore_source(
            primitives, environment["request"], tx
        )

        assert code == 0
        result = _read_json(tx / "linux-manager-switch.result.json")
        assert result["value"] == RESULT_SOURCE_RESTORED
        manager = primitives.by_name["dicepp-manager"]
        assert manager.cid == "c" * 64 and manager.running is True
        tags = [op for op in primitives.ops if op[0] == "tag"]
        assert len(tags) == 2
        assert all("dicepp-current" in op[1] for op in tags)

    async def test_finalize_committed_requires_commit(self, environment) -> None:
        with pytest.raises(HelperError, match="commit"):
            await run_finalize_committed(
                environment["primitives"], environment["request"], environment["tx_dir"]
            )

    async def test_finalize_committed_overwrites_restore_failed_and_deletes_backup(
        self, environment
    ) -> None:
        tx = environment["tx_dir"]
        primitives = environment["primitives"]
        write_decision(
            tx / "linux-manager-switch.decision.json",
            decision_payload(DECISION_COMMIT),
        )
        source = primitives.by_id["c" * 64]
        await primitives.rename(source.cid, "dicepp-manager.aaaaaaaa")
        await primitives.create(
            "dicepp-manager",
            {"Image": MANAGER_NEW_IMG, "Labels": {}, "HostConfig": {}},
            extra_labels={
                "io.dicepp.upgrade-transaction": "a" * 32,
                "io.dicepp.upgrade-role": "manager",
            },
            restart_policy="no",
        )
        write_result(
            tx / "linux-manager-switch.result.json",
            result_payload(
                RESULT_RESTORE_FAILED,
                error={
                    "container_id": "f" * 64,
                    "image_id": MANAGER_NEW_IMG,
                    "summary": "Docker socket request failed",
                },
            ),
            root=tx,
        )

        code = await run_finalize_committed(
            primitives, environment["request"], tx
        )

        assert code == 0
        result = _read_json(tx / "linux-manager-switch.result.json")
        assert result["value"] == RESULT_TARGET_COMMITTED
        assert "error" not in result
        assert "c" * 64 not in primitives.by_id
        target = primitives.by_name["dicepp-manager"]
        assert target.cid == "new-dicepp-manager"
        assert target.restart == "unless-stopped"

    async def test_finalize_committed_accepts_already_deleted_bound_backup(
        self, environment
    ) -> None:
        primitives = environment["primitives"]
        request = environment["request"]
        source = primitives.by_id["c" * 64]
        await primitives.rename(source.cid, "dicepp-manager.aaaaaaaa")
        await primitives.create(
            "dicepp-manager",
            {"Image": MANAGER_NEW_IMG, "Labels": {}, "HostConfig": {}},
            extra_labels={
                "io.dicepp.upgrade-transaction": "a" * 32,
                "io.dicepp.upgrade-role": "manager",
            },
            restart_policy="no",
        )
        await primitives.delete(source.cid)

        await _finalize_committed(
            primitives,
            request,
            source_backup_id=source.cid,
        )

        target = primitives.by_name["dicepp-manager"]
        assert target.cid == "new-dicepp-manager"
        assert target.restart == "unless-stopped"

    @pytest.mark.parametrize(
        "success_value", [RESULT_TARGET_COMMITTED, RESULT_SOURCE_RESTORED]
    )
    async def test_failed_finalize_keeps_successful_result(
        self, environment, success_value
    ) -> None:
        """R7: 失败现场不得把已存在的成功终态覆盖为 restore-failed。"""
        tx = environment["tx_dir"]
        primitives = environment["primitives"]
        write_decision(
            tx / "linux-manager-switch.decision.json",
            decision_payload(DECISION_COMMIT),
        )
        write_result(
            tx / "linux-manager-switch.result.json",
            result_payload(success_value),
            root=tx,
        )
        # 目标 Manager 不存在(宿主机重启后 restart=no 未拉起,正式名仍被
        # 来源备份占着)→ finalize 失败,走 _write_failed_result
        code = await run_finalize_committed(
            primitives, environment["request"], tx
        )

        assert code == 1
        result = _read_json(tx / "linux-manager-switch.result.json")
        # 成功终态保留,失败未被覆盖
        assert result["value"] == success_value
        assert "error" not in result


class TestCli:
    def test_main_requires_transaction_dir(self) -> None:
        with pytest.raises(SystemExit):
            main(["--socket", "/tmp/none.sock"])

    def test_main_missing_request_returns_error(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        code = main(
            [
                "--transaction-dir",
                str(empty),
                "--socket",
                "/tmp/none.sock",
                "--mode",
                "switch",
            ]
        )
        assert code == 2
