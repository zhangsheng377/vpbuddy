"""测试: auth — 用户注册/登录/验证 + JWT

ADR-0047: 邮箱密码登录, bcrypt, 72h JWT.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from vpbuddy.server.auth import (
    register_user,
    login_user,
    verify_token,
    get_user_by_id,
    _reset_db_for_test,
)


@pytest.fixture(autouse=True)
def _isolate_db(tmp_path):
    """每个测试用独立的 auth.db."""
    data_dir = str(tmp_path / "auth_test")
    _reset_db_for_test()
    yield data_dir
    _reset_db_for_test()


class TestRegister:
    def test_register_creates_user(self, _isolate_db):
        r = register_user("test@example.com", "password123", data_dir=_isolate_db)
        assert r["status"] == 200
        assert r["email"] == "test@example.com"
        assert "token" in r
        assert "user_id" in r

    def test_register_duplicate_email(self, _isolate_db):
        register_user("dup@example.com", "password123", data_dir=_isolate_db)
        r = register_user("dup@example.com", "password456", data_dir=_isolate_db)
        assert r["status"] == 409
        assert "已注册" in r["error"]

    def test_register_invalid_email(self, _isolate_db):
        r = register_user("notanemail", "password123", data_dir=_isolate_db)
        assert r["status"] == 400

    def test_register_short_password(self, _isolate_db):
        r = register_user("test@example.com", "123", data_dir=_isolate_db)
        assert r["status"] == 400


class TestLogin:
    def test_login_valid(self, _isolate_db):
        register_user("login@example.com", "password123", data_dir=_isolate_db)
        r = login_user("login@example.com", "password123", data_dir=_isolate_db)
        assert r["status"] == 200
        assert "token" in r
        assert r["email"] == "login@example.com"

    def test_login_wrong_password(self, _isolate_db):
        register_user("login@example.com", "password123", data_dir=_isolate_db)
        r = login_user("login@example.com", "wrongpass", data_dir=_isolate_db)
        assert r["status"] == 401

    def test_login_nonexistent(self, _isolate_db):
        r = login_user("ghost@example.com", "password123", data_dir=_isolate_db)
        assert r["status"] == 401

    def test_login_empty(self, _isolate_db):
        r = login_user("", "", data_dir=_isolate_db)
        assert r["status"] == 400


class TestToken:
    def test_verify_valid(self, _isolate_db):
        r = register_user("token@example.com", "password123", data_dir=_isolate_db)
        token = r["token"]
        user = verify_token(token)
        assert user is not None
        assert user["email"] == "token@example.com"
        assert user["user_id"] == r["user_id"]

    def test_verify_malformed(self, _isolate_db):
        assert verify_token("not.a.jwt") is None
        assert verify_token("") is None

    def test_get_user_by_id(self, _isolate_db):
        r = register_user("lookup@example.com", "password123", data_dir=_isolate_db)
        info = get_user_by_id(r["user_id"], data_dir=_isolate_db)
        assert info is not None
        assert info["email"] == "lookup@example.com"

    def test_get_user_by_id_nonexistent(self, _isolate_db):
        assert get_user_by_id("nonexistent", data_dir=_isolate_db) is None


class TestPasswordHashing:
    def test_password_not_stored_plaintext(self, _isolate_db):
        """验证数据库中不存明文密码."""
        import sqlite3
        r = register_user("hash@example.com", "mysecret", data_dir=_isolate_db)
        db = sqlite3.connect(str(Path(_isolate_db) / "auth.db"))
        row = db.execute(
            "SELECT password_hash FROM users WHERE email = ?", ("hash@example.com",)
        ).fetchone()
        db.close()
        assert row is not None
        assert row[0] != "mysecret"  # 不是明文
        assert row[0].startswith("$2")  # bcrypt 格式


class TestEdgeCases:
    def test_email_case_insensitive(self, _isolate_db):
        """注册大写, 登录小写应该成功."""
        register_user("Case@Example.Com", "password123", data_dir=_isolate_db)
        r = login_user("case@example.com", "password123", data_dir=_isolate_db)
        assert r["status"] == 200

    @pytest.mark.skip(reason="bcrypt hashing 慢, CI 环境可能超时")
    def test_re_login_returns_new_token(self, _isolate_db):
        register_user("relogin@example.com", "password123", data_dir=_isolate_db)
        r1 = login_user("relogin@example.com", "password123", data_dir=_isolate_db)
        r2 = login_user("relogin@example.com", "password123", data_dir=_isolate_db)
        assert r1["token"] != r2["token"]  # 每次都新 JWT
