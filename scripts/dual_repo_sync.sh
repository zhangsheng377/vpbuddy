#!/usr/bin/env bash
# dual_repo_sync.sh — 双仓 (本机 ext4 + GPU 端独立 git 仓) 硬同步 (2026-06-29)
#
# 张胜东反馈背景:
#   江苏联通出口 git push/pull 经常 134s timeout, 跑 vpbuddy-*/ 命令必卡.
#   客户端 LLM 没卡 (跑通), GPU 端只卡 git 协议.
#
# 设计: 走 rsync 直接同步 working tree + git reset --hard 到指定 SHA.
#       不用 git pull/fetch (走 HTTPS 在江苏联通经常抽风).
#
# 用法:
#   bash scripts/dual_repo_sync.sh [remote_user@remote_host] [remote_path] [ref_sha]
#   默认: zsd@192.168.10.63 /home/zsd/vpbuddy (当前 HEAD)
#
# 步骤:
#   1. SSH 测连通 (0.5s, 确认不是网络问题)
#   2. rsync 推 working tree (排除 .git, __pycache__, PHASE* 等垃圾)
#   3. SSH 端 git reset --hard 到指定 SHA (引用对齐)
#   4. SSH 端 head 显示新 SHA 验证
#
# 适用场景:
#   - 本机改完代码, 推 GPU 端跑测试 / 部署
#   - 本机 commit + push GitHub 后, 让 GPU 端仓 HEAD 跟上 (绕 git pull)
#   - 新机器 clone vpbuddy 后, 强制对齐本机版本
#
# 不适用:
#   - GPU 端有未提交的运行时改动 (会丢 — 强制 reset --hard)
#   - 想保留 GPU 端自己的分支 (本脚本 reset 到指定 SHA)
#
# 详见 vpbuddy-hermes-native skill § "v2 路径变更" + "第 11 轮铁律 双仓同步"

set -euo pipefail

REMOTE="${1:-zsd@192.168.10.63}"
REMOTE_PATH="${2:-/home/zsd/vpbuddy}"
REF_SHA="${3:-$(git rev-parse HEAD)}"

echo "=================================================="
echo "  VPBuddy 双仓硬同步"
echo "  本机 → $REMOTE:$REMOTE_PATH"
echo "  ref:  ${REF_SHA:0:12}"
echo "=================================================="

# 1. SSH 测连通
echo ""
echo "[1/4] SSH 测连通..."
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$REMOTE" 'echo "ssh ok"' >/dev/null 2>&1; then
    echo "❌ SSH 连不上 $REMOTE — 检查网络/key"
    exit 1
fi
echo "  ✓ SSH 通"

# 2. rsync 推 working tree
echo ""
echo "[2/4] rsync working tree (排除 .git / __pycache__ / PHASE* 垃圾)..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 重要: cd 到 repo root (脚本可能在任意 cwd 被调)
cd "$REPO_ROOT"

# 校验 cwd 是 git repo (避免 rsync 把非 vpbuddy 内容推到 GPU 端)
if ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "❌ 当前 cwd 不是 git repo: $REPO_ROOT"
    exit 3
fi

rsync -a --delete \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='docs/PHASE*' \
    --exclude='docs/STREAM*' \
    --exclude='docs/DEBUG*' \
    --exclude='docs/E2E_INTEGRATION_TEST*' \
    "$REPO_ROOT/" "$REMOTE:$REMOTE_PATH/"

# 2b. rsync .git/objects 里本 commit 用到的对象 (绕开江苏联通 git fetch 卡)
#     顺序: 先 rsync working tree (排除了 .git), 再单独 rsync .git/objects
#     REF_SHA 的 commit + tree + 所有 blob 对象, 推到 GPU 端 .git/objects
echo ""
echo "[2b] rsync .git/objects (REF_SHA 用到的 commit/tree/blob 对象)..."
OBJECTS=$(git rev-list --objects --all "$REF_SHA" 2>/dev/null | awk '{print $1}' | sort -u)
if [ -n "$OBJECTS" ]; then
    OBJ_COUNT=$(echo "$OBJECTS" | wc -l)
    # 用 rsync --files-from 拉这些文件 (从本机 .git/objects 推到 GPU 端 .git/objects)
    TMP_OBJ_LIST=$(mktemp)
    echo "$OBJECTS" > "$TMP_OBJ_LIST"
    # 把 sha 转成 .git/objects/aa/bb... 路径
    TMP_OBJ_PATHS=$(mktemp)
    while read -r sha; do
        [ -z "$sha" ] && continue
        echo ".git/objects/${sha:0:2}/${sha:2}"
    done < "$TMP_OBJ_LIST" > "$TMP_OBJ_PATHS"
    rsync -a --files-from="$TMP_OBJ_PATHS" "$REPO_ROOT/" "$REMOTE:$REMOTE_PATH/" 2>/dev/null || true
    rm -f "$TMP_OBJ_LIST" "$TMP_OBJ_PATHS"
    echo "  ✓ 推了 ~$OBJ_COUNT 个 git 对象 (commit/tree/blob)"
fi
echo "  ✓ rsync 推完"

# 3. 远端 reset HEAD
echo ""
echo "[3/4] 远端 git reset --hard $REF_SHA ..."
if ssh "$REMOTE" "cd $REMOTE_PATH && git cat-file -t $REF_SHA 2>/dev/null" | grep -q commit; then
    # 远端已有这个对象, 直接 reset
    ssh "$REMOTE" "cd $REMOTE_PATH && git reset --hard $REF_SHA 2>&1 | tail -3"
    echo "  ✓ HEAD 对齐 (本地已有 commit 对象)"
else
    # 远端没这个对象 — 江苏联通 git fetch 经常卡, 用 gh api fallback 拉
    echo "  远端缺 $REF_SHA, 尝试 gh api 注入..."
    if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
        # 用 GitHub REST API 拿 commit pack, 远端 git fetch 注入
        echo "  gh api fallback — 提示用户本机跑:"
        echo "    gh api repos/zhangsheng377/vpbuddy/git/commits/$REF_SHA | \\"
        echo "        jq -r '.tree.sha, .parents[].sha, .sha' | git fetch-pack --include-tag"
        echo "  或更简单: rsync .git/objects 到 GPU 端 (但 .git 排除在 rsync 里了)"
    else
        echo "  ⚠️  远端缺 commit 对象, 但 gh CLI 不可用"
    fi
    echo "  兜底: rsync working tree 已推, HEAD 仍指向老 SHA"
    echo "  远端代码 = 新 working tree + 老 HEAD, 文件可用, git log 会混淆"
    echo "  修法: 本机跑 git pull origin main 后重试 sync"
fi

# 4. 验证
echo ""
echo "[4/4] 验证远端 HEAD..."
NEW_HEAD=$(ssh "$REMOTE" "cd $REMOTE_PATH && git rev-parse HEAD")
echo "  本机: ${REF_SHA:0:12}"
echo "  GPU:  ${NEW_HEAD:0:12}"
if [ "$NEW_HEAD" = "$REF_SHA" ]; then
    echo ""
    echo "✅ 同步完成"
else
    echo ""
    echo "❌ SHA 不匹配, 远端可能需要手动 git pull"
    exit 2
fi
