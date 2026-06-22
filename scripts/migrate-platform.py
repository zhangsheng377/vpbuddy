#!/usr/bin/env python3
"""migrate-platform.py — 一次性数据迁移(2026-06-22)

历史背景: ADR-0008 (2026-06-21) 删除了 Platform.FEISHU enum 成员(飞书不再是真实数据源,
数据源 = VP 桌面客户端麦克风/系统音频 loopback)。但 data/meetings/*.json 中已有的 state
文件 platform 还是 "feishu",加载时 Pydantic 验证失败:
    Input should be 'local', 'tencent', 'dingtalk' or 'wecom'
    [type=enum, input_value='feishu', input_type=str]

本脚本:把所有 state 文件的 platform: "feishu" 改成 "local"(ADR-0004 + ADR-0008 落地)。

用法:
    python scripts/migrate-platform.py [--dry-run] [--data-dir DIR]

默认 --data-dir = $VPBUDDY_DATA_DIR 或 /home/zsd/vpbuddy/data/meetings

安全:
- 默认 --dry-run 提示会改哪些文件(不真改)
- --no-dry-run 才真改
- 改前自动备份 <file>.pre-migrate.bak
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="迁移 state 文件的 platform 字段")
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("VPBUDDY_DATA_DIR", "/home/zsd/vpbuddy/data/meetings"),
        help="State 文件目录(默认 $VPBUDDY_DATA_DIR 或 /home/zsd/vpbuddy/data/meetings)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="只显示会改的文件,不真改(默认开启)",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="真改(覆盖默认 dry-run)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"❌ 目录不存在: {data_dir}")
        sys.exit(1)

    # 找 state 文件
    state_files = sorted(data_dir.glob("*.json"))
    if not state_files:
        print(f"⚠️  没找到 state 文件: {data_dir}")
        sys.exit(0)

    # 找需要迁移的
    to_migrate = []
    for f in state_files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            platform = data.get("platform")
            if platform == "feishu":
                to_migrate.append((f, data))
        except Exception as e:
            print(f"⚠️  读 {f.name} 失败: {e}")

    if not to_migrate:
        print(f"✅ {len(state_files)} 个 state 文件,无需迁移(没有 platform=feishu)")
        sys.exit(0)

    # 显示
    print(f"找到 {len(to_migrate)} 个需要迁移的文件(platform=feishu → local):")
    for f, _ in to_migrate:
        print(f"  - {f.name}")

    if args.dry_run:
        print()
        print("DRY RUN:不会真改。重跑加 --no-dry-run 执行。")
        sys.exit(0)

    # 真改 + 备份
    for f, data in to_migrate:
        # 备份
        bak = f.with_suffix(f.suffix + ".pre-migrate.bak")
        shutil.copy2(f, bak)

        # 改
        data["platform"] = "local"
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ {f.name} → local (备份: {bak.name})")

    print()
    print(f"✅ 迁移完成:{len(to_migrate)} 个文件")


if __name__ == "__main__":
    main()