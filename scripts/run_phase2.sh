#!/bin/bash
# 顺序跑 5 个非 demo 子 session
set -e
export VPBUDDY_DATA_DIR=/tmp/vpbuddy_data/meetings
export VPBUDDY_DOCS_DIR=/tmp/vpbuddy_docs
export PYTHONPATH=/tmp/vpbuddy_work/src

LOG=/tmp/vpbuddy_phase2/run.log
mkdir -p /tmp/vpbuddy_phase2

echo "=== Phase 2: 5 个非 demo 子 session 端到端测试 ===" > $LOG
date >> $LOG

for kind in req arch tasks api risk; do
  echo "" | tee -a $LOG
  echo "=== Running $kind ($(date +%H:%M:%S)) ===" | tee -a $LOG
  cd /tmp/vpbuddy_work
  timeout 480 python -c "
import os, json, sys
os.environ['VPBUDDY_DATA_DIR']='/tmp/vpbuddy_data/meetings'
os.environ['VPBUDDY_DOCS_DIR']='/tmp/vpbuddy_docs'
from vpbuddy.sub_session_controller import trigger_sub_session
r = trigger_sub_session('PHASE2_TEST', '$kind', dry_run=False)
print('TRIGGERED:', r['triggered'])
if r.get('error'):
    print('ERROR:', r['error'][:300])
if r.get('hermes_output'):
    print('HERMES_OUTPUT (last 500):', r['hermes_output'][:500])
" 2>&1 | tee -a $LOG
  echo "Done $kind ($(date +%H:%M:%S))" | tee -a $LOG
done

echo "" | tee -a $LOG
echo "=== Final docs ===" | tee -a $LOG
ls -la /tmp/vpbuddy_docs/PHASE2_TEST/ 2>&1 | tee -a $LOG
echo "=== Log end $(date) ===" | tee -a $LOG
