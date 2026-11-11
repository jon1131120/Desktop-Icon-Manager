#!/usr/bin/env python3
"""403 网段外实测：起第二个沙箱中枢（:18891），ALLOWED_NETS 只含 10.0.0.0/8
→ 从 127.0.0.1 连 → 必须 403（真机零触碰，不改生产代码，只改沙箱副本）"""
import re, pathlib, subprocess, time, urllib.request, urllib.error, os, sys

src = pathlib.Path("/tmp/sandbox_minimax/hub-server.py").read_text()
src = src.replace(
    'ALLOWED_NETS = [ipaddress.ip_network(n) for n in\n                ("127.0.0.1/32", "192.168.113.0/24", "192.168.191.0/24")]',
    'ALLOWED_NETS = [ipaddress.ip_network(n) for n in ("10.0.0.0/8",)]')
assert "10.0.0.0/8" in src, "ALLOWED_NETS patch failed"
p = pathlib.Path("/tmp/sandbox_minimax/hub-server-403.py")
p.write_text(src)

home = "/tmp/sandbox_minimax/hubhome403"
os.makedirs(home, exist_ok=True)
open(f"{home}/hub-token.txt", "w").write("sandbox-token-123")

env = dict(os.environ, QSPACE_HUB_PORT="18891", QSPACE_HUB_HOME=home)
proc = subprocess.Popen([sys.executable, str(p)], env=env,
                        stdout=open("/tmp/sandbox_minimax/hub403.log", "w"),
                        stderr=subprocess.STDOUT)
time.sleep(1.8)

def req(path="/layout", token="sandbox-token-123"):
    h = {"X-Hub-Token": token} if token else {}
    r = urllib.request.Request(f"http://127.0.0.1:18891{path}", headers=h)
    try:
        return urllib.request.urlopen(r, timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code

results = {}
results["GET /layout 来自 127.0.0.1（不在白名单）"] = req()
results["GET /status 来自 127.0.0.1（不在白名单）"] = req("/status")
# 正确 token 但网段外 → 403（证明先判网段）
results["正确 token + 网段外"] = req(token="sandbox-token-123")
# 错误 token + 网段外 → 仍 403（网段优先于 token）
results["错误 token + 网段外"] = req(token="wrong")
# POST 也一样
import json
body = json.dumps({"syncAt": int(time.time())+10, "sourceMachine":"MBP",
                   "items":[{"name":"x","col":1,"row":1}]}).encode()
r = urllib.request.Request("http://127.0.0.1:18891/layout", data=body,
                           headers={"X-Hub-Token":"sandbox-token-123","Content-Type":"application/json"},
                           method="POST")
try:
    results["POST /layout 网段外"] = urllib.request.urlopen(r, timeout=5).status
except urllib.error.HTTPError as e:
    results["POST /layout 网段外"] = e.code

ok = True
for k, v in results.items():
    exp = 403
    good = (v == exp)
    ok = ok and good
    print(f"  {'✓' if good else '✗'} {k} → HTTP {v}（期望 {exp}）")

proc.terminate()
try: proc.wait(timeout=5)
except Exception: proc.kill()
print(f"\n403 网段外实测: {'PASS' if ok else 'FAIL'}")
sys.exit(0 if ok else 1)
