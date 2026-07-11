import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('47.100.182.3', port=16159, username='root', password='bUIZcWZfI1h0smfn', timeout=20)

# 2) Kill old → start new → check log
stdin, stdout, stderr = ssh.exec_command("pkill -f 'vpbuddy ui' 2>/dev/null; rm -f /tmp/api_ref /tmp/vpbuddy_ui.log; sleep 1; echo 'killed'", timeout=5)
print("KILL:", stdout.read().decode().strip())

stdin, stdout, stderr = ssh.exec_command("cd /data/vpbuddy/server && DASHSCOPE_API_KEY=sk-your-key-here BAILIAN_API_KEY=sk-your-key-here nohup /data/vpbuddy/venv/bin/vpbuddy ui --port 8765 > /tmp/vpbuddy_ui.log 2>&1 < /dev/null & echo PID=$!", timeout=5)
out = stdout.read().decode().strip()
err = stderr.read().decode().strip()
print("START:", out[:100], err[:100])

time.sleep(15)

# Check if running
stdin, stdout, stderr = ssh.exec_command("ps aux | grep 'vpbuddy ui' | grep -v grep | awk '{print $2}'; echo '---'; cat /tmp/vpbuddy_ui.log | tail -10", timeout=5)
print("STATUS:\n" + stdout.read().decode().strip()[:500])

time.sleep(12)
stdin, stdout, stderr = ssh.exec_command("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/healthz", timeout=5)
print("HEALTHZ:", stdout.read().decode().strip())

ssh.close()
