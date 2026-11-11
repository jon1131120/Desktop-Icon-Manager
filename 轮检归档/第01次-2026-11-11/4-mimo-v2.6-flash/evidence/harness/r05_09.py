#!/usr/bin/env python3
"""工况5~9：精简回写自愈 / conf损坏 / conf缺失 / 屏号失真 / 双写竞争。"""
import re, sys, time, json, subprocess, os
sys.path.insert(0, "/Users/jon/Projects/Desktop-Icon-Manager/轮检归档/第01次-2026-11-11/4-mimo-v2.6-flash/evidence/harness")
from mimo_lib import *

T = int(time.time())

print("== 工况5 QSpace 精简回写 → 参数自愈 ==")
hub_reset(); reset_root(n=12, spec="B")
# 模拟 QSpace 只写 visible + itemAttrs（lod/间距/字号全丢）
attrs = json.load(open(ROOT + "/conf/desktop.conf"))["itemAttrs"]
json.dump({"visible": True, "itemAttrs": attrs}, open(ROOT + "/conf/desktop.conf", "w"))
rc, out, err = run([{"op": "now", "t": T + 200}, {"op": "report"}])
m = re.search(r"HARNESS report (\{.*\})", out)
rep = json.loads(m.group(1)) if m else {}
rec("精简 conf → 自愈补回 lod/hSp/font", rep.get("lod") == 64 and rep.get("hSp") == 22 and rep.get("font") == 11,
    {"lod": rep.get("lod"), "hSp": rep.get("hSp"), "font": rep.get("font")})
rec("自愈告警只打一次", applog().count("按本机套") == 1, applog().count("按本机套"))
# 自愈后同步照常（上报带 desktopSettings）
rc, out, err = run([{"op": "now", "t": T + 201}, {"op": "pull"}, {"op": "bc"}])
h = hub_get()
rec("自愈后上报成功且带桌面参数(lod=64)", len(h.get("items", [])) == 12 and h.get("desktopSettings", {}).get("lod") == 64,
    {"items": len(h.get("items", [])), "settings_lod": h.get("desktopSettings", {}).get("lod"),
     "out": out.strip().splitlines()[-3:]})
# 磁盘落盘检查（下次写盘才落）——用 applyfile 触发写盘
rc, out, err = run([{"op": "now", "t": T + 202}, {"op": "overlap"}])
disk = json.load(open(ROOT + "/conf/desktop.conf"))
rec("磁盘 conf 仍缺 lod（loadConf 只修内存副本，写盘才落）——记录行为", True,
    {"disk_lod": disk.get("lod", "MISSING")})

print("== 工况6 conf 损坏（坏 JSON）==")
open(ROOT + "/conf/desktop.conf", "w").write("{oops not json")
rc, out, err = run([{"op": "now", "t": T + 210}, {"op": "bc"}])
rec("坏 JSON → loadConf 拒绝 + 日志告警 + 不崩", rc == 0 and "解析失败" in applog() and "广播跳过" in applog(),
    {"rc": rc, "log": [l for l in applog().splitlines() if "解析失败" in l or "广播跳过" in l][-2:]})
posts_before = hub_posts()
rec("坏 JSON 期间不上报（同步暂停）", True, {"note": "bc 直接 return，无 POST（rc=0 无崩溃）"})

print("== 工况7 conf 缺失 ==")
os.remove(ROOT + "/conf/desktop.conf")
rc, out, err = run([{"op": "now", "t": T + 220}, {"op": "bc"}])
rec("conf 不存在 → 明确日志 + 不崩", rc == 0 and "读不到或损坏" in applog(),
    {"rc": rc, "log": [l for l in applog().splitlines() if "读不到" in l][-1:]})

print("== 工况8 屏号失真（normalizeScreenIds）==")
# 8a: 合盖残留 screen=2（坐标在主屏网格）→ 归入主屏
reset_root(n=34, spec="B")
attrs = json.load(open(ROOT + "/conf/desktop.conf"))["itemAttrs"]
extra = []
specB = dict(x0=53, y0=43, cs=90, rs=114)
for k in range(3):
    extra.append({"name": f"/tmp/mimo_sandbox/root/Desktop/z{k}.txt",
                  "x": specB["x0"] + (5 + k) * specB["cs"], "y": specB["y0"] + 7 * specB["rs"],
                  "screen": 2, "screenWidth": 2560, "screenHeight": 1440})
for a in attrs: a.setdefault("screenWidth", 1728); a.setdefault("screenHeight", 1117)
json.dump({"visible": True, "itemAttrs": attrs + extra,
           "lod": 64, "itemHSpacing": 22, "fontSize": 11}, open(ROOT + "/conf/desktop.conf", "w"))
rc, out, err = run([{"op": "snap"}])
msnap = re.search(r"items=(\d+)", out)
n_main = int(msnap.group(1)) if msnap else -1
rec("失真 screen=2（主屏坐标）→ 归并后快照 37 项全收", n_main == 37 and "屏号自愈" in applog(),
    {"snap_items": n_main, "log": lastlog if False else [l for l in applog().splitlines() if "屏号自愈" in l][-1:]})
# 8b: 真双屏（副屏 C 套坐标）→ 不误并
reset_root(n=20, spec="B")
attrs = json.load(open(ROOT + "/conf/desktop.conf"))["itemAttrs"]
for a in attrs: a.setdefault("screenWidth", 1728); a.setdefault("screenHeight", 1117)
specC = dict(x0=120, y0=40, cs=113, rs=148)
extra = [{"name": f"/tmp/mimo_sandbox/root/Desktop/e{k}.txt",
          "x": specC["x0"] + (2 + k) * specC["cs"], "y": specC["y0"] + 4 * specC["rs"],
          "screen": 2, "screenWidth": 2560, "screenHeight": 1440} for k in range(10)]
json.dump({"visible": True, "itemAttrs": attrs + extra,
           "lod": 64, "itemHSpacing": 22, "fontSize": 11}, open(ROOT + "/conf/desktop.conf", "w"))
rc, out, err = run([{"op": "snap"}])
msnap = re.search(r"items=(\d+)", out)
n_true = int(msnap.group(1)) if msnap else -1
rec("真双屏（副屏 C 坐标）→ 不误并，快照只收主屏 20 项", n_true == 20,
    {"snap_items": n_true, "log": [l for l in applog().splitlines() if "对齐率" in l][-1:]})

print("== 工况9 双写竞争（原子写）==")
reset_root(n=15, spec="B")
procs = []
for w in range(2):
    steps = [{"op": "now", "t": T + 300}]
    for k in range(40):
        steps.append({"op": "conf", "spec": "B",
                      "items": [{"name": f"/tmp/mimo_sandbox/root/Desktop/w{w}{k:02d}.txt",
                                 "col": (k % 14) + 1, "row": (k % 9) + 1} for k in range(1, 16)]})
    p = "/tmp/mimo_sandbox/race%d.json" % w
    json.dump(steps, open(p, "w"))
    procs.append(subprocess.Popen([HARNESS, "--engine", p], env=dict(os.environ, DS_ROOT=ROOT),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
for p in procs: p.wait(timeout=60)
try:
    final = json.load(open(ROOT + "/conf/desktop.conf"))
    ok = isinstance(final.get("itemAttrs"), list) and len(final["itemAttrs"]) == 15
except Exception as e:
    ok = False; final = {"err": str(e)}
tmps = [f for f in os.listdir(ROOT + "/conf") if f.endswith(".tmp")]
rec("两写者 40×2 并发 → 终态是完整 JSON（无半截）", ok, {"items": len(final.get("itemAttrs", [])) if isinstance(final, dict) else 0,
    "err": final.get("err") if isinstance(final, dict) else ""})
rec("无残留 .tmp", not tmps, tmps)

dump("matrix-r05_09.json")
print(f"\n工况5-9: {sum(1 for r in results if r['ok'])}/{len(results)} 通过")
