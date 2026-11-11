#!/usr/bin/env python3
"""验证修复补丁真的修好了（打补丁后的台架二进制 + 补丁版 hub 实例）。"""
import json, os, shutil, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dslib as D
from patched_edits import P1_OLD, P1_NEW, P2_OLD, P2_NEW, P3_OLD, P3_NEW

H = os.path.dirname(os.path.abspath(__file__))
T0 = D.now()
ALIAS = list(json.load(open("/tmp/ds_sandbox/fixtures/rename_map.json")).keys())

src = open(os.path.join(H, "main_harness.swift")).read()
for a, b in ((P1_OLD, P1_NEW), (P2_OLD, P2_NEW), (P3_OLD, P3_NEW)):
    assert a in src, a[:60]
    src = src.replace(a, b, 1)
open("/tmp/ds_sandbox/harness_patched.swift", "w").write(src)
r = subprocess.run(["swiftc", "-O", "-o", "/tmp/ds_sandbox/harness_fixed",
                    "/tmp/ds_sandbox/harness_patched.swift"], capture_output=True, text=True)
D.check("补丁台架编译通过", r.returncode == 0, r.stderr[-400:])
D.HARNESS = "/tmp/ds_sandbox/harness_fixed"

# ---- P2 VIP 尾随空格归位 ----
r1 = D.root("pv_vip", "MBP")
c = D.conf_from_names(r1, ALIAS[:5], "C")
c["itemAttrs"].append({"name": "/Volumes/Jon113  ", "x": 233, "y": 40, "z": 0, "screen": "1",
                       "screenWidth": 2560, "screenHeight": 1410})
D.write_conf(r1, c)
D.engine(r1, [{"op": "now", "t": T0}, {"op": "relocate"}])
pos = [a for a in D.read_conf(r1)["itemAttrs"] if a["name"].startswith("/Volumes/Jon113")]
cell = (round((pos[0]["x"] - 120) / 113) + 1, round((pos[0]["y"] - 40) / 148) + 1) if pos else None
D.check("P2 修复验证：/Volumes/Jon113␣␣ 归位 (22,2)", cell == (22, 2), cell)

# ---- P1 中段区全满不死循环 ----
r2 = D.root("pv_mid", "MBP")
c2 = D.conf_from_names(r2, ALIAS[:3], "C")
k = 0
for col in range(12, 18):
    for row in range(1, 10):
        c2["itemAttrs"].append({"name": f"/Volumes/fill{k:02d}", "x": 120 + (col - 1) * 113,
                                "y": 40 + (row - 1) * 148, "z": 0, "screen": "1",
                                "screenWidth": 2560, "screenHeight": 1410})
        k += 1
c2["itemAttrs"].append({"name": "/Volumes/lastone", "x": 2493, "y": 40, "z": 0, "screen": "1",
                        "screenWidth": 2560, "screenHeight": 1410})
D.write_conf(r2, c2)
sp = "/tmp/ds_sandbox/reloc_fix.json"
json.dump([{"op": "now", "t": T0}, {"op": "relocate"}], open(sp, "w"))
t0 = time.time()
try:
    o = subprocess.run([D.HARNESS, "--engine", sp], capture_output=True, text=True,
                       env={**os.environ, "DS_ROOT": r2}, timeout=12)
    dt = time.time() - t0
    D.check("P1 修复验证：中段区全满 12s 内返回（旧实现死循环）", dt < 12, f"{dt:.1f}s")
    D.check("P1 修复验证：日志明确「中段区已满，暂不搬移」", "中段区已满" in D.log_text(r2), D.log_text(r2)[-300:])
    D.check("P1 修复验证：不落重复格", not D.overlaps(D.read_conf(r2), r2), D.overlaps(D.read_conf(r2), r2))
except subprocess.TimeoutExpired:
    D.check("P1 修复验证：中段区全满 12s 内返回", False, "timeout")

# ---- P3(hub) syncAt 类型 → 400 ----
hub_fixed = "/tmp/ds_sandbox/patchwork/hub_fixed.py"
shutil.rmtree("/tmp/ds_sandbox/datafix", ignore_errors=True)
os.makedirs("/tmp/ds_sandbox/datafix", exist_ok=True)
open("/tmp/ds_sandbox/datafix/hub-token.txt", "w").write(D.TOKEN)
proc = subprocess.Popen(["python3", hub_fixed], env={**os.environ, "QSPACE_HUB_DIR": "/tmp/ds_sandbox/datafix"},
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(1.5)
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", 18892, timeout=5)
    body = json.dumps({"syncAt": "abc", "sourceMachine": "MBP", "items": [{"name": "a", "col": 1, "row": 1}]})
    conn.request("POST", "/layout", body=body,
                 headers={"Content-Type": "application/json", "X-Hub-Token": D.TOKEN})
    resp = conn.getresponse()
    D.check("P3 修复验证：syncAt 字符串 → 400（旧实现 500）", resp.status == 400,
            (resp.status, resp.read()[:120]))
    conn.close()
finally:
    proc.terminate()

# ---- P2(hubServerTime) 钟快自愈 ----
r3 = D.root("pv_fast", "Studio")
D.write_conf(r3, D.conf_from_names(r3, ALIAS[:10], "C"))
D.write_state(r3, {"baselineFingerprint": "stale", "lastBroadcastFingerprint": "stale", "lastAppliedAt": T0})
D.engine(r3, [{"op": "now", "t": T0 + 400}, {"op": "bc"}])
log = D.log_text(r3)
hub = D.hub_get()
D.check("P2 修复验证：钟快 400s 时 hubServerTime 可用（日志有「本机时钟超前」）",
        "本机时钟超前" in log, log[-400:])
D.check("P2 修复验证：用中枢时间重发后被接受", hub.get("sourceMachine") == "Studio", hub.get("sourceMachine"))

bad = sum(1 for x in D.RESULTS if not x["ok"])
D.save("patch-verify")
print("FAIL:", bad)
sys.exit(1 if bad else 0)
