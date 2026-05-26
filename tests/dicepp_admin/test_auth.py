"""auth.py 测试 — 密码 hash + session 生命周期 + 过期清理（pear #45 Q1）"""
import time

import pytest


class TestPasswordHashing:
    def test_set_then_verify_succeeds(self, tmp_admin_paths):
        from dicepp_admin import auth
        auth.set_password("hunter2hunter")
        assert auth.verify_password("admin", "hunter2hunter") is True

    def test_verify_wrong_password_fails(self, tmp_admin_paths):
        from dicepp_admin import auth
        auth.set_password("hunter2hunter")
        assert auth.verify_password("admin", "wrong-password") is False

    def test_verify_wrong_username_fails(self, tmp_admin_paths):
        from dicepp_admin import auth
        auth.set_password("hunter2hunter")
        assert auth.verify_password("other_user", "hunter2hunter") is False

    def test_short_password_rejected(self, tmp_admin_paths):
        from dicepp_admin import auth
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            auth.set_password("12345")  # < 6
        assert ei.value.status_code == 400

    def test_hash_uses_unique_salt(self, tmp_admin_paths):
        """同密码两次 set，hash 不同（salt 随机）"""
        from dicepp_admin import auth
        import json
        auth.set_password("samepassword")
        h1 = json.loads(auth.AdminPaths.AUTH_FILE.read_text())["password_hash"]
        auth.set_password("samepassword")
        h2 = json.loads(auth.AdminPaths.AUTH_FILE.read_text())["password_hash"]
        assert h1 != h2, "salt 没起作用，两次 hash 相同"


class TestSessionLifecycle:
    def test_create_and_get_session(self, tmp_admin_paths):
        from dicepp_admin import auth
        token = auth.create_session("admin")
        s = auth.get_session(token)
        assert s is not None
        assert s["username"] == "admin"

    def test_get_unknown_token_returns_none(self, tmp_admin_paths):
        from dicepp_admin import auth
        assert auth.get_session("nonexistent-token") is None

    def test_revoke_session(self, tmp_admin_paths):
        from dicepp_admin import auth
        token = auth.create_session("admin")
        auth.revoke_session(token)
        assert auth.get_session(token) is None

    def test_expired_session_returns_none(self, tmp_admin_paths, monkeypatch):
        """手工把 session 过期时间调到过去，get_session 应返回 None"""
        from dicepp_admin import auth
        token = auth.create_session("admin")
        # 把 expires_at 改到 1 小时前
        sessions = auth._load_sessions()
        sessions[token]["expires_at"] = int(time.time()) - 3600
        auth._save_sessions(sessions)
        assert auth.get_session(token) is None


class TestGetSessionPruneExpired:
    """pear #45 Q1 回归：get_session 应该清理所有过期 session，防止
    sessions.json 长期膨胀。"""

    def test_get_session_prunes_other_expired_entries(self, tmp_admin_paths, monkeypatch):
        from dicepp_admin import auth
        # 创建 3 个 session，其中 2 个手工标记过期
        t1 = auth.create_session("admin")
        t2 = auth.create_session("admin")
        t3 = auth.create_session("admin")
        sessions = auth._load_sessions()
        sessions[t1]["expires_at"] = int(time.time()) - 3600  # 过期
        sessions[t2]["expires_at"] = int(time.time()) - 3600  # 过期
        # t3 不动 — 仍有效
        auth._save_sessions(sessions)

        # 访问 t3，应该清掉 t1 + t2
        s = auth.get_session(t3)
        assert s is not None
        remaining = auth._load_sessions()
        assert t3 in remaining
        assert t1 not in remaining, "get_session 未清理过期 session t1"
        assert t2 not in remaining, "get_session 未清理过期 session t2"

    def test_get_session_with_expired_target_also_prunes(self, tmp_admin_paths):
        """当查询的 token 本身已过期，仍应清理其他过期项"""
        from dicepp_admin import auth
        t1 = auth.create_session("admin")
        t2 = auth.create_session("admin")
        sessions = auth._load_sessions()
        sessions[t1]["expires_at"] = int(time.time()) - 3600
        sessions[t2]["expires_at"] = int(time.time()) - 3600
        auth._save_sessions(sessions)

        result = auth.get_session(t1)
        assert result is None
        remaining = auth._load_sessions()
        assert t1 not in remaining
        assert t2 not in remaining

    def test_no_save_when_nothing_to_prune(self, tmp_admin_paths, monkeypatch):
        """没有过期项时，不应触发 _save_sessions 写盘（避免无谓 IO）"""
        from dicepp_admin import auth
        t = auth.create_session("admin")
        call_count = {"n": 0}
        original_save = auth._save_sessions

        def counted_save(data):
            call_count["n"] += 1
            original_save(data)

        monkeypatch.setattr(auth, "_save_sessions", counted_save)
        auth.get_session(t)
        assert call_count["n"] == 0, "无过期项时不应该写盘"
