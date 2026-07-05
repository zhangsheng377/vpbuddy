import paramiko, io, json, sys

results = {}

try:
    jump = paramiko.SSHClient()
    jump.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    jump.connect('192.168.10.5', username='zsd', password='292929', timeout=15)
    results['jump_connect'] = 'OK'
except Exception as e:
    results['jump_connect'] = f'FAIL: {e}'
    print(json.dumps(results, indent=2, ensure_ascii=False))
    sys.exit(1)

try:
    stdin, stdout, stderr = jump.exec_command('cat /home/zsd/.ssh/hermes_47.100.182.3_ed25519', timeout=10)
    key_data = stdout.read()
    err = stderr.read().decode().strip()
    if not key_data:
        results['read_key'] = f'FAIL: key empty, stderr: {err}'
        print(json.dumps(results, indent=2, ensure_ascii=False))
        sys.exit(1)
    key_file = io.StringIO(key_data.decode())
    private_key = paramiko.Ed25519Key.from_private_key(key_file)
    results['read_key'] = 'OK'
except Exception as e:
    results['read_key'] = f'FAIL: {e}'
    print(json.dumps(results, indent=2, ensure_ascii=False))
    sys.exit(1)

try:
    jump_transport = jump.get_transport()
    dest_addr = ('47.100.182.3', 16159)
    src_addr = ('192.168.10.5', 0)
    channel = jump_transport.open_channel('direct-tcpip', dest_addr, src_addr, timeout=30)
    results['tunnel'] = 'OK'
except Exception as e:
    results['tunnel'] = f'FAIL: {e}'
    print(json.dumps(results, indent=2, ensure_ascii=False))
    sys.exit(1)

try:
    target = paramiko.SSHClient()
    target.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    target.connect('47.100.182.3', username='root', sock=channel, pkey=private_key, timeout=15)
    results['target_connect'] = 'OK'
except Exception as e:
    results['target_connect'] = f'FAIL: {e}'
    print(json.dumps(results, indent=2, ensure_ascii=False))
    sys.exit(1)

commands = {
    1: "ps -ef | grep vpbuddy.ui_server | grep -v grep",
    2: "cat /tmp/_vpbuddy_env",
    3: """head -50 /data/vpbuddy/server/src/vpbuddy/sub_session_controller.py | grep -n '_get_or_create_agent\\|batch_docs\\|parent_session\\|ephemeral_system'""",
    4: """grep -n 'DOCS_DIR\\|VPBUDDY_DOCS_DIR' /data/vpbuddy/server/src/vpbuddy/ui_server.py | head -10""",
    5: "cat /data/vpbuddy/server/src/vpbuddy/_version.py",
    6: "grep version /data/vpbuddy/server/pyproject.toml | head -5",
    7: "hermes --version 2>&1"
}

for num, cmd in commands.items():
    try:
        stdin, stdout, stderr = target.exec_command(cmd, timeout=30)
        out = stdout.read().decode('utf-8', errors='replace').strip()
        err = stderr.read().decode('utf-8', errors='replace').strip()
        rc = stdout.channel.recv_exit_status()
        results[str(num)] = {'stdout': out, 'stderr': err, 'exit_code': rc}
    except Exception as e:
        results[str(num)] = {'error': str(e)}

target.close()
jump.close()
print(json.dumps(results, indent=2, ensure_ascii=False))
