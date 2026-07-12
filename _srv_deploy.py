import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('47.100.182.3', port=16159, username='root', password='bUIZcWZfI1h0smfn', timeout=15)

env_content = """DASHSCOPE_API_KEY=sk-your-key-here
BAILIAN_API_KEY=sk-your-key-here
OPENAI_API_KEY=sk-your-key-here
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
"""

hermes_config = """model:
  default: minimax-m3
  provider: minimax

auxiliary:
  vision:
    provider: openai
    model: qwen-vl-max
    timeout: 60
"""

# Write .env
stdin, stdout, stderr = ssh.exec_command("cat > /data/vpbuddy/server/.env", timeout=5)
stdin.write(env_content)
stdin.close()
stdout.read()
stderr.read()

# Write hermes config
stdin, stdout, stderr = ssh.exec_command("mkdir -p ~/.hermes && cat > ~/.hermes/config.yaml", timeout=5)
stdin.write(hermes_config)
stdin.close()

# Verify
stdin, stdout, stderr = ssh.exec_command("""
echo ".env: $(wc -c < /data/vpbuddy/server/.env) bytes"
echo "hermes: $(wc -c < ~/.hermes/config.yaml) bytes"
cat ~/.hermes/config.yaml
""", timeout=10)
print(stdout.read().decode(errors='replace'))

# Restart
ssh.exec_command("for pid in $(pgrep -f vpbuddy); do kill -9 $pid 2>/dev/null; done; sleep 2", timeout=10)
ssh.exec_command("cd /data/vpbuddy/server && nohup bash run.sh > /tmp/vpbuddy_ui.log 2>&1 &", timeout=5)
time.sleep(10)

stdin, stdout, stderr = ssh.exec_command("""
PID=$(pgrep -f vpbuddy | head -1)
echo "PID=$PID"
echo "HEALTH=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8765/healthz)"
cat /proc/$PID/environ | tr '\\0' '\\n' | grep -c DASHSCOPE
""", timeout=10)
print(stdout.read().decode(errors='replace'))
ssh.close()
