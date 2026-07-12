import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect('47.100.182.3', port=16159, username='root', password='bUIZcWZfI1h0smfn', timeout=15)

ssh.exec_command("""
cat > /root/.hermes/config.yaml << 'EOF'
model:
  default: minimax-m3
  provider: minimax

auxiliary:
  vision:
    provider: custom
    model: qwen-vl-max
    timeout: 60
EOF
echo "CONFIG UPDATED: provider=custom"
cat /root/.hermes/config.yaml
""", timeout=10)

stdin, stdout, stderr = ssh.exec_command("""
echo "=== TEST: vision via custom endpoint ==="
export OPENAI_API_KEY=sk-your-key-here
export OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
/data/vpbuddy/venv/bin/python3.11 -c "
from hermes_cli.config import cfg_get, load_config
cfg = load_config()
print('vision config:', cfg.get('auxiliary', {}).get('vision', {}))

# Direct test: create client like _try_custom_endpoint would
import os
from agent.auxiliary_client import _create_openai_client

client = _create_openai_client(
    api_key=os.environ['OPENAI_API_KEY'],
    base_url=os.environ['OPENAI_BASE_URL']
)
import base64
img_path = '/data/vpbuddy/server/data/meetings/uploads/test_202607122054/3f2f7c85455d_20260712-165554.jpg'
with open(img_path,'rb') as f:
    b64 = base64.b64encode(f.read()).decode()

resp = client.chat.completions.create(
    model='qwen-vl-max',
    messages=[{'role':'user','content':[
        {'type':'text','text':'一句话描述'},
        {'type':'image_url','image_url':{'url':f'data:image/jpeg;base64,{b64}'}}
    ]}],
    max_tokens=30,
    timeout=20
)
print('status: 200')
print('reply:', resp.choices[0].message.content[:100])
" 2>&1

echo ""
echo "=== kill hermes for restart ==="
pkill -f hermes-agent 2>/dev/null
echo "done"
""", timeout=40)
print(stdout.read().decode(errors='replace'))
ssh.close()
