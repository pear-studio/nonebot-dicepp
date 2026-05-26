"""instance_manager.py 测试 — 端口分配 + CRUD + 锁安全 + token 不可预测"""
import threading

import pytest


class TestPortAllocation:
    def test_allocate_skips_existing(self, tmp_admin_paths):
        from dicepp_admin import instance_manager as im
        port = im._allocate_port(existing=[im.INSTANCE_PORT_START])
        assert port != im.INSTANCE_PORT_START
        assert port > im.INSTANCE_PORT_START

    def test_allocate_returns_in_range(self, tmp_admin_paths):
        from dicepp_admin import instance_manager as im
        port = im._allocate_port(existing=[])
        assert im.INSTANCE_PORT_START <= port <= im.INSTANCE_PORT_END

    def test_raise_when_all_ports_taken(self, tmp_admin_paths, monkeypatch):
        from dicepp_admin import instance_manager as im
        # 把搜索范围压到 1 个端口然后占掉
        monkeypatch.setattr(im, "INSTANCE_PORT_START", 65000)
        monkeypatch.setattr(im, "INSTANCE_PORT_END", 65000)
        with pytest.raises(RuntimeError, match="全部占用"):
            im._allocate_port(existing=[65000])


class TestInstanceCRUD:
    def test_create_basic(self, tmp_admin_paths):
        from dicepp_admin import instance_manager as im
        inst = im.create_instance("test-bot")
        assert inst["name"] == "test-bot"
        assert "port" in inst and inst["port"] >= im.INSTANCE_PORT_START
        assert inst["running"] is False
        assert inst["access_token"]
        # 数据目录已建好
        from pathlib import Path
        assert Path(inst["data_dir"]).exists()

    def test_create_empty_name_raises(self, tmp_admin_paths):
        from dicepp_admin import instance_manager as im
        with pytest.raises(ValueError, match="不能为空"):
            im.create_instance("   ")

    def test_list_returns_created(self, tmp_admin_paths):
        from dicepp_admin import instance_manager as im
        im.create_instance("a")
        im.create_instance("b")
        names = [x["name"] for x in im.list_instances()]
        assert "a" in names
        assert "b" in names

    def test_update_changes_name(self, tmp_admin_paths):
        from dicepp_admin import instance_manager as im
        inst = im.create_instance("old-name")
        im.update_instance(inst["id"], {"name": "new-name"})
        got = im.get_instance(inst["id"])
        assert got["name"] == "new-name"

    def test_delete_removes_from_listing(self, tmp_admin_paths):
        from dicepp_admin import instance_manager as im
        inst = im.create_instance("doomed")
        im.delete_instance(inst["id"], remove_data=False)
        assert im.get_instance(inst["id"]) is None

    def test_delete_with_remove_data_cleans_dir(self, tmp_admin_paths):
        from dicepp_admin import instance_manager as im
        from pathlib import Path
        inst = im.create_instance("doomed-with-data")
        data_dir = Path(inst["data_dir"])
        assert data_dir.exists()
        im.delete_instance(inst["id"], remove_data=True)
        assert not data_dir.exists()


class TestDeleteOrderingS2:
    """pear #45 S2 回归：delete_instance 先做磁盘清理再写 instances.json，
    防止 rmtree 失败时 instances.json 已删条目但磁盘残留"""

    def test_instances_json_updated_after_disk_cleanup(self, tmp_admin_paths, monkeypatch):
        from dicepp_admin import instance_manager as im
        from pathlib import Path
        inst = im.create_instance("ordered-delete")
        inst_id = inst["id"]
        data_dir = Path(inst["data_dir"])

        # 跟踪调用顺序
        call_order = []
        original_rmtree = im.shutil.rmtree
        original_save = im._save_instances

        def tracked_rmtree(path, **kw):
            call_order.append(f"rmtree({path})")
            return original_rmtree(path, **kw)

        def tracked_save(data):
            call_order.append(f"save({sorted(data.keys())})")
            return original_save(data)

        monkeypatch.setattr(im.shutil, "rmtree", tracked_rmtree)
        monkeypatch.setattr(im, "_save_instances", tracked_save)

        im.delete_instance(inst_id, remove_data=True)

        # 确认顺序：rmtree 必须在 _save_instances（清除 inst_id）之前
        rmtree_idx = next(i for i, c in enumerate(call_order) if "rmtree" in c)
        save_idx = next(i for i, c in enumerate(call_order)
                        if "save" in c and inst_id not in c)
        assert rmtree_idx < save_idx, \
            f"rmtree 必须先于 save(without inst_id)，实际: {call_order}"


class TestAccessTokenSecurityQ2:
    """pear #45 Q2 回归：access_token 应该不可预测（CSPRNG），不是时间戳推断"""

    def test_token_is_hex_format(self, tmp_admin_paths):
        from dicepp_admin import instance_manager as im
        inst = im.create_instance("token-test")
        token = inst["access_token"]
        assert len(token) == 32, f"期望 32 hex chars，实际 {len(token)}"
        assert all(c in "0123456789abcdef" for c in token), \
            f"token 含非 hex 字符: {token}"

    def test_two_tokens_differ(self, tmp_admin_paths):
        from dicepp_admin import instance_manager as im
        a = im.create_instance("a")["access_token"]
        b = im.create_instance("b")["access_token"]
        assert a != b

    def test_token_not_predictable_from_timestamp(self, tmp_admin_paths):
        """token 应该跟时间戳无关，毫秒时间戳全数字，token 应有字母"""
        from dicepp_admin import instance_manager as im
        inst = im.create_instance("predictability-test")
        token = inst["access_token"]
        # CSPRNG hex token 几乎肯定包含字母（a-f）
        assert any(c in "abcdef" for c in token), \
            f"token 看起来像时间戳（全数字）: {token}"


class TestProcessLockSafetyS1:
    """pear #45 S1 回归：_processes 字典并发读写应该串行化"""

    def test_lock_exists(self, tmp_admin_paths):
        from dicepp_admin import instance_manager as im
        assert hasattr(im, "_processes_lock")
        # 检查是 RLock（支持同一线程重入）
        assert isinstance(im._processes_lock, type(threading.RLock()))

    def test_concurrent_is_running_no_crash(self, tmp_admin_paths):
        """模拟多线程同时调 is_running，不应 KeyError 或 race"""
        from dicepp_admin import instance_manager as im

        inst = im.create_instance("concurrent-test")
        inst_id = inst["id"]
        # 手工塞个 ref 让 is_running 走分支
        from dicepp_admin.instance_manager import ProcessRef
        with im._processes_lock:
            im._processes[inst_id] = ProcessRef(pid=99999999)  # 假 pid 必死

        errors = []

        def hammer():
            try:
                for _ in range(100):
                    im.is_running(inst_id)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=hammer) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"并发 is_running 出错: {errors}"
