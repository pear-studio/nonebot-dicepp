"""llonebot_manager.py 测试 — config 生成 + auto_acquire 备份/还原"""
import json
from pathlib import Path

import pytest


class TestGenerateConfig:
    """generate_config 把 QQ 实例的反向 WS 地址写入 LLOneBot config_<qq>.json"""

    def _setup_bundle(self, tmp_path: Path) -> Path:
        """模拟一个最小 LLOneBot bundle 让 bundle_dir() 返回它"""
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        # llbot.exe 是 bundle_dir() 的判别条件
        (bundle / "llbot.exe").write_text("")
        (bundle / "data").mkdir()
        return bundle

    def test_writes_correct_url(self, tmp_admin_paths, tmp_path, monkeypatch):
        from dicepp_admin import llonebot_manager as lm
        bundle = self._setup_bundle(tmp_path)
        monkeypatch.setattr(lm, "_BUNDLE_DIR_DEFAULT", bundle)
        lm.set_llbot_path(str(bundle / "llbot.exe"))

        result = lm.generate_config("12345678", instance_port=8080,
                                     access_token="abc")
        assert result["status"] == "ok"
        # 检查文件
        cfg_file = bundle / "data" / "config_12345678.json"
        assert cfg_file.exists()
        cfg = json.loads(cfg_file.read_text())
        # 找到 ws-reverse 项验证 url + token
        ws_reverse = next(
            (c for c in cfg["ob11"]["connect"]
             if c.get("type") == "ws-reverse" and c.get("enable")),
            None,
        )
        assert ws_reverse is not None
        assert ws_reverse["url"] == "ws://127.0.0.1:8080/onebot/v11/ws"
        assert ws_reverse["token"] == "abc"

    def test_rejects_non_numeric_qq(self, tmp_admin_paths, tmp_path, monkeypatch):
        from dicepp_admin import llonebot_manager as lm
        bundle = self._setup_bundle(tmp_path)
        monkeypatch.setattr(lm, "_BUNDLE_DIR_DEFAULT", bundle)
        lm.set_llbot_path(str(bundle / "llbot.exe"))
        result = lm.generate_config("not-a-qq", 8080, "tok")
        assert result["status"] == "invalid_qq"

    def test_no_bundle_returns_no_bundle(self, tmp_admin_paths, tmp_path, monkeypatch):
        from dicepp_admin import llonebot_manager as lm
        # 把 bundle 指向一个不存在的路径，确保 bundle_dir() 返回 None
        monkeypatch.setattr(lm, "_BUNDLE_DIR_DEFAULT", tmp_path / "nope")
        lm.set_llbot_path("")  # 清掉手动指定路径
        result = lm.generate_config("12345", 8080, "tok")
        assert result["status"] == "no_bundle"

    def test_preserves_other_config_keys(self, tmp_admin_paths, tmp_path, monkeypatch):
        """若 config_<qq>.json 已存在,写新 ws-reverse 时应保留其他字段"""
        from dicepp_admin import llonebot_manager as lm
        bundle = self._setup_bundle(tmp_path)
        monkeypatch.setattr(lm, "_BUNDLE_DIR_DEFAULT", bundle)
        lm.set_llbot_path(str(bundle / "llbot.exe"))

        # 预置一个 config 含自定义字段
        cfg_file = bundle / "data" / "config_99999.json"
        cfg_file.write_text(json.dumps({
            "ob11": {"enable": True, "connect": []},
            "custom_user_field": "must-survive",
        }))

        lm.generate_config("99999", 9001, "token9")
        updated = json.loads(cfg_file.read_text())
        assert updated.get("custom_user_field") == "must-survive"


class TestClearConfig:
    def test_clear_existing(self, tmp_admin_paths, tmp_path, monkeypatch):
        from dicepp_admin import llonebot_manager as lm
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        (bundle / "llbot.exe").write_text("")
        data = bundle / "data"
        data.mkdir()
        (data / "config_777.json").write_text("{}")
        monkeypatch.setattr(lm, "_BUNDLE_DIR_DEFAULT", bundle)
        lm.set_llbot_path(str(bundle / "llbot.exe"))

        assert lm.clear_config("777") is True
        assert not (data / "config_777.json").exists()

    def test_clear_nonexistent_returns_false(self, tmp_admin_paths, tmp_path, monkeypatch):
        from dicepp_admin import llonebot_manager as lm
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        (bundle / "llbot.exe").write_text("")
        (bundle / "data").mkdir()
        monkeypatch.setattr(lm, "_BUNDLE_DIR_DEFAULT", bundle)
        lm.set_llbot_path(str(bundle / "llbot.exe"))
        assert lm.clear_config("000-not-there") is False


class TestAutoAcquireLoginPreservationS4:
    """pear #45 S4 关注点：auto_acquire 重新复制 bundle 时不能丢用户登录态。

    通过 end-to-end 走一遍 auto_acquire：
    1. 准备一个 src bundle（模拟用户本地已有的 LLOneBot 整合包）
    2. 准备一个 dst bundle（模拟项目里之前已经 acquire 过，含登录的 data/）
    3. 跑 auto_acquire 应该让 dst 被新 src 替换，但 data/ 里 config_<qq>.json 保留
    """

    def test_existing_login_data_preserved(self, tmp_admin_paths, tmp_path, monkeypatch):
        """模拟「旧 bundle 损坏（缺 llbot.exe）但 data/ 还在」的恢复场景。

        is_acquired() 返回 False（缺 llbot.exe），所以走完整 acquire 流程；
        但 dst.exists() 是 True 且 data/ 有内容，触发 backup/restore 路径。
        最后验证：复制完新 bundle 后，data/config_<qq>.json 仍然在。
        """
        from dicepp_admin import llonebot_manager as lm

        # 1. 准备「项目同级 LLONEBOT」作为扫描源（含 llbot.exe）
        src = tmp_path / "external_LLONEBOT"
        src.mkdir()
        (src / "llbot.exe").write_text("fresh")
        (src / "bin").mkdir()
        (src / "data").mkdir()

        # 2. 准备 dst 半残状态：有 data/config 但 llbot.exe 不在
        dst = tmp_path / "bundle"
        dst.mkdir()
        # 注意：故意不写 llbot.exe，让 is_acquired() 返回 False
        dst_data = dst / "data"
        dst_data.mkdir()
        (dst_data / "config_555.json").write_text('{"logged_in": true}')

        monkeypatch.setattr(lm, "_BUNDLE_DIR_DEFAULT", dst)
        monkeypatch.setattr(lm, "_SCAN_CANDIDATES", [src])
        lm.set_llbot_path("")  # 清掉手动指定路径，避免影响 is_acquired

        result = lm.auto_acquire()
        assert result["status"] == "acquired", f"实际: {result}"
        # 关键：用户已登录的 config 必须还在
        assert (dst / "data" / "config_555.json").exists(), \
            "auto_acquire 丢了用户登录态 config_<qq>.json"
        content = (dst / "data" / "config_555.json").read_text()
        assert '"logged_in": true' in content

    def test_auto_acquire_skips_when_already_acquired(self, tmp_admin_paths, tmp_path, monkeypatch):
        from dicepp_admin import llonebot_manager as lm
        dst = tmp_path / "bundle"
        dst.mkdir()
        (dst / "llbot.exe").write_text("present")
        monkeypatch.setattr(lm, "_BUNDLE_DIR_DEFAULT", dst)
        lm.set_llbot_path(str(dst / "llbot.exe"))

        result = lm.auto_acquire()
        assert result["status"] == "already_acquired"

    def test_auto_acquire_when_no_local_source(self, tmp_admin_paths, tmp_path, monkeypatch):
        from dicepp_admin import llonebot_manager as lm
        dst = tmp_path / "nope_bundle"
        monkeypatch.setattr(lm, "_BUNDLE_DIR_DEFAULT", dst)
        # 所有扫描位置都指向不存在的路径
        monkeypatch.setattr(lm, "_SCAN_CANDIDATES",
                            [tmp_path / "nowhere1", tmp_path / "nowhere2"])
        lm.set_llbot_path("")
        result = lm.auto_acquire()
        assert result["status"] == "not_found"


class TestListConfiguredQqs:
    def test_returns_empty_when_no_bundle(self, tmp_admin_paths):
        from dicepp_admin import llonebot_manager as lm
        assert lm.list_configured_qqs() == []

    def test_lists_all_config_files(self, tmp_admin_paths, tmp_path, monkeypatch):
        from dicepp_admin import llonebot_manager as lm
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        (bundle / "llbot.exe").write_text("")
        data = bundle / "data"
        data.mkdir()
        # 合法 QQ 配置 + 非配置文件 + 非法名
        (data / "config_111.json").write_text(json.dumps({
            "ob11": {"connect": [
                {"type": "ws-reverse", "enable": True, "url": "ws://x/y"}
            ]}
        }))
        (data / "config_222.json").write_text(json.dumps({
            "ob11": {"connect": []}
        }))
        (data / "irrelevant.json").write_text("{}")
        (data / "config_not_a_number.json").write_text("{}")
        monkeypatch.setattr(lm, "_BUNDLE_DIR_DEFAULT", bundle)
        lm.set_llbot_path(str(bundle / "llbot.exe"))

        qqs = lm.list_configured_qqs()
        qq_ids = [q["qq_id"] for q in qqs]
        assert "111" in qq_ids
        assert "222" in qq_ids
        assert "not_a_number" not in qq_ids
