import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('47.100.182.3', port=16159, username='root', password='bUIZcWZfI1h0smfn', timeout=20)

stdin, stdout, stderr = ssh.exec_command("""
# 在服务器上试试能不能编译含有 windows crate 的版本
cd /data/vpbuddy/server
git stash 2>/dev/null && git checkout cedd33e 2>&1 || true
git checkout main 2>/dev/null
""", timeout=10)
print("PREP:", stdout.read().decode().strip())

# 直接在 vpbuddy-client 目录试试 cargo check
stdin, stdout, stderr = ssh.exec_command("""
cd /data/vpbuddy/server/vpbuddy-client/src-tauri 2>/dev/null || echo "NO CLIENT DIR"
ls Cargo.toml 2>/dev/null && head -3 Cargo.toml || echo "NO CARGO TOML"
""", timeout=10)
print("CLIENT:", stdout.read().decode().strip())

ssh.close()
