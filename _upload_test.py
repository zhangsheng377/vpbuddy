import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('47.100.182.3', port=16159, username='root', password='bUIZcWZfI1h0smfn', timeout=15)

sftp = ssh.open_sftp()
local_file = 'C:/Users/43587/Desktop/codes/vpbuddy/src/tests/e2e/test_improvements_api_e2e.py'
remote_file = '/data/vpbuddy/server/src/tests/e2e/test_improvements_api_e2e.py'
sftp.put(local_file, remote_file)
sftp.close()
print("测试文件已上传")

cmd = "cd /data/vpbuddy/server && RUN_E2E=1 /data/vpbuddy/venv/bin/python -m pytest src/tests/e2e/test_improvements_api_e2e.py -v --tb=short"
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=600)
rc = stdout.channel.recv_exit_status()
out = stdout.read().decode('utf-8', errors='replace').strip()
err = stderr.read().decode('utf-8', errors='replace').strip()
print("\n测试输出:\n", out)
if err:
    print("\n错误输出:\n", err)
print("\n测试返回码:", rc)

ssh.close()
print("\n完成")