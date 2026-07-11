import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('47.100.182.3', port=16159, username='root', password='bUIZcWZfI1h0smfn', timeout=15)

sftp = ssh.open_sftp()
local_file = 'C:/Users/43587/Desktop/codes/vpbuddy/src/vpbuddy/server/fastapi_app.py'
remote_file = '/data/vpbuddy/server/src/vpbuddy/server/fastapi_app.py'
sftp.put(local_file, remote_file)
sftp.close()
print("fastapi_app.py 已上传")

print("重启服务...")
cmd = '''
fuser -k 8765/tcp 2>/dev/null || true
sleep 2
cd /data/vpbuddy/server && nohup /data/vpbuddy/venv/bin/vpbuddy ui --port 8765 > /tmp/vpbuddy_ui.log 2>&1 &
sleep 10
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/api/status
'''
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=60)
rc = stdout.channel.recv_exit_status()
out = stdout.read().decode('utf-8', errors='replace').strip()
print("结果:", out)

ssh.close()
print("\n完成")