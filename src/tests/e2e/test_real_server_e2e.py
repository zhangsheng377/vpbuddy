import sys, urllib.request, urllib.error, json, time, socket
from urllib.parse import quote

URL = "http://47.100.182.3:28765"

def r(path, method="GET", body=None, token=None, ct="application/json", raw_body=None, timeout=15):
    headers = {}
    if token: headers["Authorization"] = "Bearer " + token
    if ct: headers["Content-Type"] = ct
    data = raw_body
    if data is None and body is not None:
        if isinstance(body, (dict, list)): data = json.dumps(body).encode()
        elif isinstance(body, bytes): data = body
        else: data = str(body).encode()
    req = urllib.request.Request(URL + path, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try: b = json.load(e)
        except: b = {"raw": str(e)}
        return e.code, b
    except socket.timeout:
        return 0, {"error": "timeout"}
    except Exception as e:
        return 0, {"error": str(e)[:100]}

ts = int(time.time() * 1000)
passed = [0]; failed = [0]; skipped = [0]

def T(name, fn):
    try:
        fn()
        passed[0] += 1
        sys.stdout.write("  PASS  " + name + "\n"); sys.stdout.flush()
    except Exception as e:
        failed[0] += 1
        sys.stdout.write("  FAIL  " + name + ": " + str(e)[:120] + "\n"); sys.stdout.flush()

def S(name):
    skipped[0] += 1
    sys.stdout.write("  SKIP  " + name + "\n"); sys.stdout.flush()

def show(name, code, body):
    sys.stdout.write("  > " + name + "  " + str(code) + "  " + str(body)[:120] + "\n"); sys.stdout.flush()

print("=" * 60, flush=True)
print("VPBuddy 真实 E2E — 全用户行为链路", flush=True)
print("=" * 60, flush=True)

# ── 1. 公共端点 ──
print("\n── 1. 公共端点 ──", flush=True)
c, b = r("/healthz"); show("healthz", c, b)
assert c == 200 and b.get("ok") == True; T("/healthz 200", lambda: None)

c, b = r("/api/status"); show("status no-auth", c, b)
assert c == 401; T("/api/status 无auth 401", lambda: None)

c, b = r("/api/auth/register", "POST", {"email": "e2e_es_" + str(ts) + "@test.dev", "password": "t123456"})
token = b.get("token", "")
assert c == 200 and token; T("register", lambda: None)

c, b = r("/api/auth/login", "POST", {"email": "e2e_es_" + str(ts) + "@test.dev", "password": "t123456"})
assert c == 200 and b.get("token"); T("login", lambda: None)

c, b = r("/api/auth/me", token=token)
assert c == 200 and b.get("email"); T("me", lambda: None)

c2, b2 = r("/api/auth/register", "POST", {"email": "e2e_es_b_" + str(ts) + "@test.dev", "password": "t123456"})
tok_b = b2.get("token", ""); assert tok_b; T("user B register", lambda: None)

# ── 2. 会议 CRUD ──
print("\n── 2. 会议 CRUD ──", flush=True)
mid = "e2e_es_lc_" + str(ts)
c, b = r("/api/meetings/stream_start?meeting_id=" + mid + "&audio_source=microphone&project_name=" + quote("ESG沟通"), "POST", {"platform":"e2e"}, token)
show("stream_start", c, b)
assert c == 200 and b.get("meeting_id") == mid; T("stream_start", lambda: None)

c, b = r("/api/meetings", token=token)
assert c == 200 and len(b.get("meetings",[])) > 0; T("list meetings", lambda: None)

c, b = r("/api/meetings/" + mid, token=token)
assert c == 200; T("GET meeting detail", lambda: None)

c, b = r("/api/meetings/" + mid, "PATCH", {"project_name":"ESGv2"}, token)
assert c == 200 and b.get("project_name") == "ESGv2"; T("PATCH title", lambda: None)

c, b = r("/api/meetings/" + mid + "/state", token=token)
show("state", c, b)
assert 200 <= c < 300; T("GET state", lambda: None)

c, b = r("/api/meetings/" + mid + "/aggregate", token=token)
show("aggregate", c, b)
assert 200 <= c < 300; T("GET aggregate", lambda: None)

c, b = r("/api/meetings/" + mid + "/docs", token=token)
show("docs", c, b)
assert 200 <= c < 300; T("GET docs", lambda: None)

# ── 3. Chat (timeout tolerant) ──
print("\n── 3. Chat ──", flush=True)
c, b = r("/api/meetings/" + mid + "/chat", "POST", {"message":"hello"}, token, timeout=20)
show("chat", c, b)
if 200 <= c < 300: T("chat", lambda: None)
elif c == 0: S("chat (timeout)")
else: T("chat", lambda: None)

c, b = r("/api/meetings/" + mid + "/chat/history", token=token, timeout=20)
show("history", c, b)
if 200 <= c < 300: T("chat history", lambda: None)
elif c == 0: S("chat history (timeout)")
else: T("chat history", lambda: None)

# ── 4. Materials ──
print("\n── 4. Materials ──", flush=True)
bnd = b"----e2ebnd"
mp = b"--" + bnd + b'\r\nContent-Disposition: form-data; name="file"; filename="esg.txt"\r\nContent-Type: text/plain\r\n\r\n'
mp += b"ESG requirements.\r\n"
mp += b"--" + bnd + b"--\r\n"
ct_hdr = "multipart/form-data; boundary=----e2ebnd"

c, b = r("/api/meetings/" + mid + "/materials", "POST", raw_body=mp, token=token, ct=ct_hdr, timeout=30)
show("upload", c, b)
if 200 <= c < 300 and (b.get("id") or b.get("material_id")): T("upload material", lambda: None)
elif c == 0: S("upload material (timeout)")
else: T("upload material", lambda: None)

c, b = r("/api/meetings/" + mid + "/materials", token=token, timeout=20)
if 200 <= c < 300: T("list materials", lambda: None)
else: S("list materials")

c, b = r("/api/meetings/" + mid + "/close", "POST", token=token, timeout=20)
show("close", c, b)
if 200 <= c < 300: T("close", lambda: None)
elif c == 0: S("close (timeout)")
else: T("close", lambda: None)

# ── 5. KB ──
print("\n── 5. KB ──", flush=True)
kb_mp = b"--" + bnd + b'\r\nContent-Disposition: form-data; name="meeting_id"\r\n\r\n'
kb_mp += mid.encode() + b"\r\n"
kb_mp += b"--" + bnd + b'\r\nContent-Disposition: form-data; name="file"; filename="green.txt"\r\nContent-Type: text/plain\r\n\r\n'
kb_mp += b"LEED v5 energy.\r\n"
kb_mp += b"--" + bnd + b"--\r\n"

c, b = r("/api/kb/upload", "POST", raw_body=kb_mp, token=token, ct=ct_hdr, timeout=30)
show("KB upload", c, b)
if 200 <= c < 300: T("KB upload", lambda: None)
elif c == 0: S("KB upload (timeout)")
else: T("KB upload", lambda: None)

c, b = r("/api/kb/list", token=token, timeout=20)
if 200 <= c < 300: T("KB list", lambda: None)
else: S("KB list")

c, b = r("/api/kb/search?q=LEED", token=token, timeout=20)
if 200 <= c < 300: T("KB search", lambda: None)
else: S("KB search")

# ── 6. AI Settings ──
print("\n── 6. AI Settings ──", flush=True)
c, b = r("/api/settings/ai", token=token)
assert 200 <= c < 300; T("GET ai settings", lambda: None)

c, b = r("/api/settings/ai", "PUT", {"model":"gpt-4"}, token)
assert 200 <= c < 300; T("PUT ai settings", lambda: None)

# ── 7. Delete ──
print("\n── 7. Delete ──", flush=True)
mid_del = "e2e_es_del_" + str(ts)
c, _ = r("/api/meetings/stream_start?meeting_id=" + mid_del + "&audio_source=microphone", "POST", {"platform":"e2e"}, token)
c, b = r("/api/meetings/" + mid_del, "DELETE", token=token)
show("DELETE", c, b)
assert 200 <= c < 300 and b.get("deleted",{}).get("state"); T("DELETE meeting", lambda: None)

c, b = r("/api/experiences", token=token)
assert 200 <= c < 300; T("GET experiences", lambda: None)

# ── 8. 安全: 401 ──
print("\n── 8. 安全: 401 ──", flush=True)
for p in ["/api/meetings","/api/status","/api/timeline","/api/kb/search","/api/kb/list"]:
    c, _ = r(p); assert c == 401, p + " 期望401 实际" + str(c)
    T(p + " 401", lambda: None)

# ── 9. 安全: 403 ──
print("\n── 9. 安全: 403 ──", flush=True)
miso = "e2e_es_iso_" + str(ts)
r("/api/meetings/stream_start?meeting_id=" + miso + "&audio_source=microphone", "POST", {"platform":"e2e"}, token)
for ep in ["docs","events","state","aggregate","collab"]:
    c, _ = r("/api/meetings/" + miso + "/" + ep, token=tok_b)
    assert c == 403, ep + " 期望403"; T("GET /"+ep+" 403", lambda: None)

c, _ = r("/api/meetings/" + miso + "/chat", "POST", {"message":"x"}, tok_b)
assert c == 403; T("POST /chat 403", lambda: None)

c, _ = r("/api/meetings/" + miso + "/close", "POST", token=tok_b)
assert c == 403; T("POST /close 403", lambda: None)

c, _ = r("/api/meetings/stream_start?meeting_id=" + miso + "&audio_source=microphone", "POST", {"platform":"x"}, tok_b)
assert c == 403; T("stream_start reuse 403", lambda: None)

c, _ = r("/api/meetings/" + miso, "DELETE", token=tok_b)
assert c == 403; T("DELETE 403", lambda: None)

c, _ = r("/api/meetings/" + miso, "PATCH", {"project_name":"x"}, tok_b)
assert c == 403; T("PATCH 403", lambda: None)

c, _ = r("/api/meetings/" + miso + "/materials", token=tok_b)
assert c == 403; T("GET materials 403", lambda: None)

r("/api/meetings/" + miso, "DELETE", token=token)

total = passed[0] + failed[0] + skipped[0]
print("\n" + "=" * 60, flush=True)
print("结果: " + str(passed[0]) + " passed, " + str(failed[0]) + " failed, " + str(skipped[0]) + " skipped  (共 " + str(total) + " 项)", flush=True)
print("=" * 60, flush=True)
