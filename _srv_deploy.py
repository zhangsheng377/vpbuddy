import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('47.100.182.3', port=16159, username='root', password='bUIZcWZfI1h0smfn', timeout=30)

# Create .env
ssh.exec_command("""
cat > /data/vpbuddy/server/.env << 'EOF'
DASHSCOPE_API_KEY=sk-your-key-here
BAILIAN_API_KEY=sk-your-key-here
OPENAI_API_KEY=sk-your-key-here
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MINIMAX_API_KEY=
EOF
echo ".env created"
""", timeout=10)

# Fix hermes vision model to qwen-vl-max (confirmed working)
ssh.exec_command("""
cat > ~/.hermes/config.yaml << 'HERMES_EOF'
model:
  default: minimax-m3
  provider: minimax

auxiliary:
  vision:
    provider: openai
    model: qwen-vl-max
    timeout: 60
  embedding:
    provider: dashscope
    model: text-embedding-v4
HERMES_EOF
echo "hermes config updated (vision → qwen-vl-max)"
cat ~/.hermes/config.yaml
""", timeout=10)

# Pull latest code + restart
stdin, stdout, stderr = ssh.exec_command("""
cd /data/vpbuddy/server && git pull origin main --ff-only 2>&1
echo "HEAD: $(git rev-parse --short HEAD)"
""", timeout=25)
print(stdout.read().decode(errors='replace'))

ssh.exec_command("for pid in $(pgrep -f vpbuddy); do kill -9 $pid 2>/dev/null; done; sleep 2", timeout=10)
ssh.exec_command("cd /data/vpbuddy/server && nohup bash run.sh > /tmp/vpbuddy_ui.log 2>&1 &", timeout=5)
time.sleep(10)

stdin, stdout, stderr = ssh.exec_command("""
PID=$(pgrep -f vpbuddy | head -1)
echo "PID=$PID"
echo "HEALTH=$(curl -s http://localhost:8765/healthz)"
cat /proc/$PID/environ 2>/dev/null | tr '\\0' '\\n' | grep -E 'DASHSCOPE|OPENAI' | head -3
echo "=== gkd line ==="
grep 'triggering docs' /tmp/vpbuddy_ui.log | tail -3
""", timeout=15)
print(stdout.read().decode(errors='replace'))
ssh.close()
