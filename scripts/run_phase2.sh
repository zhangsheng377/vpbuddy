#!/bin/bash
# 端到端跑 5 个非 demo 子 session(本地版)
# 用法: bash run_phase2.sh [--help]
#
# 设计: 在本地 /home/zsd/vpbuddy 跑,VPBUDDY_DIRECT=1 模式
# (主 session 拿 prompt 后用 write_file 写盘)
#
# 2026-06-21 适配 VPBUDDY_DIRECT 模式

set -e
PROJECT_DIR="/home/zsd/vpbuddy"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<EOF
VPBuddy 端到端测试脚本(本地版)

用法: bash run_phase2.sh [--help]

跑 5 个非 demo 子 session(req/arch/tasks/api/risk),
VPBUDDY_DIRECT=1 模式让主 session 写文件。

前置: v0.9.0: controller 已删除, 文档生成由 _close_meeting 通过 task_manager 触发
EOF
    exit 0
fi

# 默认跑 PHASE2_TEST
MEETING_ID="${MEETING_ID:-PHASE2_TEST}"
export VPBUDDY_DATA_DIR="$PROJECT_DIR/data/meetings"
export VPBUDDY_DOCS_DIR="$PROJECT_DIR/docs"
export PYTHONPATH="$PROJECT_DIR/src"
export VPBUDDY_DIRECT=1

LOG=/tmp/vpbuddy_phase2/run.log
mkdir -p /tmp/vpbuddy_phase2

echo "=== Phase 2: 5 个非 demo 子 session (MEETING=$MEETING_ID) ===" | tee $LOG
date | tee -a $LOG

for kind in req arch tasks api risk; do
  echo "" | tee -a $LOG
  echo "=== Running $kind ($(date +%H:%M:%S)) ===" | tee -a $LOG
  cd "$PROJECT_DIR"
  timeout 480 python3 -c "
import os, json, sys
os.environ['VPBUDDY_DATA_DIR']='$PROJECT_DIR/data/meetings'
os.environ['VPBUDDY_DOCS_DIR']='$PROJECT_DIR/docs'
os.environ['VPBUDDY_DIRECT']='1'
from vpbuddy.sub_session_controller import trigger_sub_session
r = trigger_sub_session('$MEETING_ID', '$kind', dry_run=False)
print('TRIGGERED:', r['triggered'])
print('DIRECT:', r.get('direct', False))
if r.get('error'):
    print('ERROR:', r['error'][:300])
if r.get('prompt'):
    print('PROMPT_LEN:', len(r['prompt']))
    print('DOC_PATH:', r.get('doc_path'))
" 2>&1 | tee -a $LOG
  echo "Done $kind ($(date +%H:%M:%S))" | tee -a $LOG
done

echo "" | tee -a $LOG
echo "=== Final docs ===" | tee -a $LOG
ls -la "$PROJECT_DIR/docs/$MEETING_ID/" 2>&1 | tee -a $LOG
echo "=== Log end $(date) ===" | tee -a $LOG
