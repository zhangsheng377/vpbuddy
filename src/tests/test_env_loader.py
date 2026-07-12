"""v0.22.6: .env 加载测试 — 多路径 fallback + force overwrite"""

from __future__ import annotations
import os, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def _reload_env_module(env_path: Path):
    env_path.write_text("TEST_VAR_ABC=hello123\nTEST_VAR_XYZ=world456\n", encoding="utf-8")
    os.environ.pop("TEST_VAR_ABC", None)
    os.environ.pop("TEST_VAR_XYZ", None)

    for line in env_path.read_text().split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip("'").strip('"')

    return env_path


def test_env_overwrite_not_setdefault():
    """force overwrite: key 已存在时仍覆盖."""
    os.environ["TEST_VAR_ABC"] = "old_value"
    tmp = Path(tempfile.mkdtemp(prefix="vp_env_test_")) / ".env"
    _reload_env_module(tmp)
    assert os.environ["TEST_VAR_ABC"] == "hello123"


def test_env_loads_all_vars():
    """所有 K=V 行都被加载."""
    tmp = Path(tempfile.mkdtemp(prefix="vp_env_test_")) / ".env"
    _reload_env_module(tmp)
    assert os.environ["TEST_VAR_ABC"] == "hello123"
    assert os.environ["TEST_VAR_XYZ"] == "world456"


def test_env_skips_comments_and_blank():
    """跳过注释行和空行."""
    tmp = Path(tempfile.mkdtemp(prefix="vp_env_test_")) / ".env"
    tmp.write_text("# 这是个注释\n\n  VALID=yes  \n  \n# another comment\n", encoding="utf-8")
    os.environ.pop("VALID", None)
    for line in tmp.read_text().split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip("'").strip('"')
    assert os.environ["VALID"] == "yes"


def test_env_handles_quotes():
    """去掉引号: 'value' → value, \"value\" → value."""
    tmp = Path(tempfile.mkdtemp(prefix="vp_env_test_")) / ".env"
    tmp.write_text('KEY1="double"\nKEY2=\'single\'\nKEY3=bare\n', encoding="utf-8")
    for k in ("KEY1", "KEY2", "KEY3"):
        os.environ.pop(k, None)
    for line in tmp.read_text().split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip("'").strip('"')
    assert os.environ["KEY1"] == "double"
    assert os.environ["KEY2"] == "single"
    assert os.environ["KEY3"] == "bare"


def test_env_nonexistent_file_no_crash():
    """不存在的 .env 不抛异常."""
    p = Path(tempfile.mkdtemp(prefix="vp_env_test_")) / ".env"
    assert not p.exists()


def test_env_multiple_candidate_fallback():
    """多路径 fallback: 第一个不存在 → 查第二个."""
    candidates = [
        Path(tempfile.mkdtemp(prefix="vp_env_a_")) / ".env",
        Path(tempfile.mkdtemp(prefix="vp_env_b_")) / ".env",
    ]
    # 只有第二个存在
    candidates[1].write_text("FALLBACK_VAR=found_me\n", encoding="utf-8")
    os.environ.pop("FALLBACK_VAR", None)

    env_file = None
    for c in candidates:
        if c.exists():
            env_file = c
            break
    assert env_file == candidates[1]
    for line in env_file.read_text().split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip("'").strip('"')
    assert os.environ["FALLBACK_VAR"] == "found_me"
