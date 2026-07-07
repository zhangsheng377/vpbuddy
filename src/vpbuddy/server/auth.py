"""auth — 用户认证: 注册/登录/验证 + JWT + SQLite

ADR-0047: 邮箱密码登录, bcrypt hashing, JWT 72h 过期.
"""

from __future__ import annotations

import sqlite3
import uuid
import bcrypt
import jwt
import time
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

UTC = timezone.utc
logger = logging.getLogger(__name__)

# ── JWT 配置 ──
JWT_SECRET = os.environ.get("VPBUDDY_JWT_SECRET", "vpbuddy-dev-secret-change-in-prod")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 72

# ── SQLite DB ──
_DB: sqlite3.Connection | None = None
_DB_PATH: Path | None = None


def _get_db(data_dir: str | Path = "") -> sqlite3.Connection:
    global _DB, _DB_PATH
    if _DB is not None:
        return _DB
    if not data_dir:
        data_dir = Path(__file__).resolve().parent.parent.parent / "data"
    db_path = Path(data_dir) / "auth.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _DB = sqlite3.connect(str(db_path), check_same_thread=False)
    _DB_PATH = db_path
    _DB.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    _DB.commit()
    return _DB


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def _create_token(user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRY_HOURS * 3600,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def register_user(email: str, password: str, data_dir: str = "") -> dict:
    """注册新用户. 返回 {user_id, email, token} 或 {error, status}."""
    email = email.strip().lower()
    if not email or "@" not in email:
        return {"error": "邮箱格式无效", "status": 400}
    if not password or len(password) < 6:
        return {"error": "密码至少 6 位", "status": 400}

    db = _get_db(data_dir)
    user_id = uuid.uuid4().hex[:16]
    now = datetime.now(UTC).isoformat()

    try:
        db.execute(
            "INSERT INTO users (id, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (user_id, email, _hash_password(password), now),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return {"error": "邮箱已注册", "status": 409}

    token = _create_token(user_id, email)
    return {"user_id": user_id, "email": email, "token": token, "status": 200}


def login_user(email: str, password: str, data_dir: str = "") -> dict:
    """登录. 返回 {user_id, email, token} 或 {error, status}."""
    email = email.strip().lower()
    if not email or not password:
        return {"error": "邮箱和密码必填", "status": 400}

    db = _get_db(data_dir)
    row = db.execute(
        "SELECT id, email, password_hash FROM users WHERE email = ?", (email,)
    ).fetchone()

    if row is None:
        return {"error": "邮箱或密码错误", "status": 401}

    user_id, db_email, pw_hash = row
    if not _verify_password(password, pw_hash):
        return {"error": "邮箱或密码错误", "status": 401}

    token = _create_token(user_id, db_email)
    return {"user_id": user_id, "email": db_email, "token": token, "status": 200}


def verify_token(token: str) -> dict | None:
    """验证 token 并返回用户信息. 无效返回 None."""
    payload = _decode_token(token)
    if payload is None:
        return None
    return {"user_id": payload["sub"], "email": payload["email"]}


def get_user_by_id(user_id: str, data_dir: str = "") -> dict | None:
    """按 ID 查用户."""
    db = _get_db(data_dir)
    row = db.execute(
        "SELECT id, email, created_at FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    if row is None:
        return None
    return {"user_id": row[0], "email": row[1], "created_at": row[2]}


def _reset_db_for_test():
    """测试用: 重置数据库连接."""
    global _DB, _DB_PATH
    _DB = None
    _DB_PATH = None
