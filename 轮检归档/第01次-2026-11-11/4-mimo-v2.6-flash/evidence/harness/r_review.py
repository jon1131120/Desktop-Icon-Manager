#!/usr/bin/env python3
"""复核实测：前三位报的高危项逐一在沙箱复现（确认「在/不在」，不改生产）。"""
import re, sys, time, json, os, subprocess, signal
sys.path.insert(0, "/Users/jon/Projects/Desktop-Icon-Manager/轮检归档/第01次-2026-11-11/4-mimo-v2.6-flash/evidence/harness")
from mimo_lib import *

T = int(time.time())
PROD = "/Users/jon/Projects/Desktop-Icon-Manager/QSpaceLayoutBar/main.swift"

print("== 复核A deepseek-P1 中段区死循环（main.swift:1022）==")
hub_reset(); reset_root(n=0, spec="B")
# 造满中段区：B 套 midZone=(10,14) → 5 列 × 9 行 = 45 格全占 + 右锚区一个临时卷宗
items = []
for c in range(10, 15):
    for r in range(1, 10):
        items.append({"name": f"/tmp/mimo_sandbox/root/Desktop/m{c}_{r}.txt",
                      "col": c, "row": r})
items.append({"name": "/Volumes/USBSTICK", "col": 15, "row": 5})   # 临时卷宗入右锚 → 触发弹
script = [{"op": "now", "t": T}, {"op": "conf", "spec": "B", "items": items},
          {"op": "relocate"}]
try:
    rc, out, err = run(script, timeout=8)
    rec("中段区占满+1临时卷宗 → relocate 8s 内返回（若此条❌=死循环实锤）",
        "CHANGED" in out or "nil" in out, {"rc": rc, "out": out.strip().splitlines()[-2:]})
except subprocess.TimeoutExpired:
    rec("中段区占满+1临时卷宗 → relocate 8s 内返回（若此条❌=死循环实锤）", False,
        "TimeoutExpired：relocateStrayVolumes 游标无界死循环复现（main.swift:1022-1026）")

print("== 复核B deepseek-P2 Jon113 尾随空格 VIP 永不归位（main.swift:1003）==")
def vip_case(name):
    reset_root(n=0, spec="B")
    its = [{"name": "/tmp/mimo_sandbox/root/Desktop/d1.txt", "col": 3, "row": 8},
           {"name": name, "col": 5, "row": 8}]          # VIP 漂在 (5,8)
    rc, out, err = run([{"op": "conf", "spec": "B", "items": its}, {"op": "relocate"}])
    conf = conf_load()
    for a in conf.get("itemAttrs", []):
        if str(a.get("name", "")).rstrip() == "/Volumes/Jon113":
            x, y = a["x"], a["y"]
            col = round((x - 53) / 90) + 1; row = round((y - 43) / 114) + 1
            return (col, row), out.strip().splitlines()[-1]
    return None, out.strip().splitlines()[-1]
pos_ok, log_ok = vip_case("/Volumes/Jon113")
pos_bad, log_bad = vip_case("/Volumes/Jon113  ")   # 尾随两空格（真机 conf 实态）
rec("对照：不带尾空格 → VIP 归位 (19,2)", pos_ok == (19, 2), {"pos": pos_ok, "log": log_ok})
rec("【deepseek-P2】带尾随空格 → 永不归位（❌=缺陷仍在）", pos_bad != (19, 2),
    {"pos": pos_bad, "log": log_bad})

print("== 复核C deepseek-P2 时钟超前 → hubServerTime 相对URL → 改动永久丢失 ==")
hub_reset(); reset_root(n=15, spec="B")
run([{"op": "now", "t": T}, {"op": "cycle"}])                 # 中枢版本 syncAt=T
fp0 = state_load().get("baselineFingerprint")
write_conf(15, spec="B", shift=5)
fp1 = re.search(r"fp=(\S+)", run([{"op": "snap"}])[1]).group(1)
run([{"op": "state", "set": {"lastAppliedAt": 0}}, {"op": "now", "t": T + 400}, {"op": "bc"}])
run([{"op": "state", "set": {"lastAppliedAt": 0}}, {"op": "now", "t": T + 400}, {"op": "bc"}])
lg = applog(); st = state_load(); h = hub_get()
rec("钟快 400s：自愈分支不可达（无「本机时钟超前」日志）", "本机时钟超前" not in lg,
    {"heal_log": [l for l in lg.splitlines() if "超前" in l]})
rec("【deepseek-P2】二次被拒后放弃本机版本 → baseline 被写成 fp1（改动丢失）",
    st.get("baselineFingerprint") == fp1 and "放弃本机版本" in lg and h.get("syncAt") == T,
    {"baseline": st.get("baselineFingerprint", "")[:12], "expect_fp1": fp1[:12],
     "hub_syncAt": h.get("syncAt"), "log": [l for l in lg.splitlines() if "放弃" in l][-1:]})

print("== 复核D glm-P1/主会话：两段式预登记（kept 排队尾不重叠）==")
hub_reset(); reset_root(n=0, spec="C")
# 远端 A 套快照：f01..f04 在 A(1..4,8)
remote = {"specKey": "A", "sourceMachine": "Air", "syncAt": T, "formatVersion": 2,
          "layoutFingerprint": "fp-remote-1",
          "items": [{"name": f"f0{i}.txt", "col": i, "row": 8} for i in range(1, 5)]}
json.dump(remote, open("/tmp/mimo_sandbox/remote_snap.json", "w"))
# 本机 C 套 conf：matched 四项 + kept.md 排队尾占住 f01 的映射落点 B(1,8)
specC = dict(x0=120, y0=40, cs=113, rs=148)
specB = dict(x0=53, y0=43, cs=90, rs=114)
its = [{"name": f"/tmp/mimo_sandbox/root/Desktop/f0{i}.txt",
        "x": specC["x0"] + (i + 4) * specC["cs"], "y": specC["y0"] + 7 * specC["rs"],
        "screen": 1, "screenWidth": 2560, "screenHeight": 1440} for i in range(1, 5)]
its.append({"name": "/tmp/mimo_sandbox/root/Desktop/kept.md",        # 队尾 kept
            "x": specB["x0"], "y": specB["y0"] + 7 * specB["rs"],     # 占 B(1,8)
            "screen": 1, "screenWidth": 1728, "screenHeight": 1117})
json.dump({"visible": True, "lod": 96, "itemHSpacing": 11, "fontSize": 11,
           "itemAttrs": its}, open(ROOT + "/conf/desktop.conf", "w"))
# 注意：kept 的 screenWidth 1728 会并入 B 组 → 用纯 C 组（2560）做主屏，kept 也写 2560
its[-1]["screenWidth"] = 2560; its[-1]["screenHeight"] = 1440
its[-1]["x"] = specC["x0"] + 0 * specC["cs"]          # kept 在 C(1,8) —— 映射后 f01 落 B(1,8)
its[-1]["y"] = specC["y0"] + 7 * specC["rs"]
json.dump({"visible": True, "lod": 96, "itemHSpacing": 11, "fontSize": 11,
           "itemAttrs": its}, open(ROOT + "/conf/desktop.conf", "w"))
rc, out, err = run([{"op": "applyfile", "path": "/tmp/mimo_sandbox/remote_snap.json", "write": True}])
conf = conf_load()
cells = {}
for a in conf.get("itemAttrs", []):
    x, y = a["x"], a["y"]
    c = round((x - specB["x0"]) / specB["cs"]) + 1; r = round((y - specB["y0"]) / specB["rs"]) + 1
    cells.setdefault((c, r), []).append(os.path.basename(a["name"]))
dups = {k: v for k, v in cells.items() if len(v) > 1}
rec("kept 排队尾占落点 → 应用后同格重叠 0（两段式预登记有效）", not dups,
    {"apply": out.strip().splitlines()[-1], "dups": dups, "cells": {str(k): v for k, v in list(cells.items())[:8]}})

print("== 复核E 主会话 R16 specForSize 最近中心 ==")
def spec_of(w, h=1117):
    reset_root(n=3, spec="B")
    its = [{"name": f"/tmp/mimo_sandbox/root/Desktop/s{i}.txt", "col": i + 2, "row": 5,
            "screenWidth": w, "screenHeight": h} for i in range(3)]
    json.dump({"visible": True, "lod": 64, "itemAttrs": its}, open(ROOT + "/conf/desktop.conf", "w"))
    rc, out, err = run([{"op": "snap"}])
    m = re.search(r"spec=(\w+)", out)
    return m.group(1) if m else "?"
got = {w: spec_of(w) for w in (1710, 1720, 1728, 1760, 1650, 2560)}
rec("specForSize 容差+最近中心（1710→A、1720/1728/1760→B、2560→C、容差外兜底）",
    got[1710] == "A" and got[1720] == "B" and got[1728] == "B" and got[1760] == "B" and got[2560] == "C",
    got)

print("== 复核F deepseek-P3 中枢被清空后客户端不自愈 ==")
hub_reset(); reset_root(n=12, spec="B")
run([{"op": "now", "t": T + 10}, {"op": "cycle"}])            # 上报 + baseline
run([{"op": "now", "t": T + 11}, {"op": "cycle"}])            # pull 设置 hubNamesSet
h_before = len(hub_get().get("items", []))
os.remove("/tmp/mimo_sandbox/data/layout.json")               # 模拟中枢被清空
run([{"op": "now", "t": T + 12}, {"op": "cycle"}])
h_after = len(hub_get().get("items", []))
rec("【deepseek-P3】中枢清空后 baseline 已对齐的客户端不再补报（❌=仍在）",
    h_before == 12 and h_after == 0,
    {"before": h_before, "after": h_after,
     "log": [l for l in applog().splitlines() if "名单对账" in l or "hub(empty)" in l][-2:]})

dump("matrix-review.json")
print(f"\n复核实测: {sum(1 for r in results if r['ok'])}/{len(results)} 通过（含记录性 ❌）")
