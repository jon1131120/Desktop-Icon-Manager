#!/usr/bin/env python3
"""夹具生成器：从**真机 conf 采样**（结构/坐标/参数逐项保留），桌面项改名 fNN 脱敏。

用法: python3 make_fixtures.py <输出目录>
产物:
  conf_real.json      真 conf 采样（桌面项改名，卷宗/VIP 原名保留）
  items_real.json     快照 items 对照（name/col/row）
  conf_a.json / conf_b.json  合成 A/B 套夹具（屏幕字段真实标注，用于跨套测试）
"""
import json, os, sys, re
from collections import Counter

REAL = "/Users/jon/Library/Application Support/com.jinghaoshe.qspace.pro/desktop.conf"
OUT = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ds_sandbox/fixtures"
os.makedirs(OUT, exist_ok=True)

d = json.load(open(REAL))
attrs = d["itemAttrs"]
REAL_DESK = "/Users/jon/Desktop/"
DESK = "{DS_ROOT}/Desktop/"   # 运行期替换为沙箱根（夹具与生产同构，路径必须落在沙箱内）

# 类 C 套（真机当前 2560x1410 外接屏）: 桌面项改名，卷宗保持原名（VIP/临时卷宗逻辑要真名）
n = 0
renamed = {}
out = []
for a in attrs:
    b = dict(a)
    nm = str(a.get("name", ""))
    if nm.startswith(REAL_DESK) and nm.split("/")[-1] != "SynologyDrive":   # VIP 桌面部保持原名
        n += 1
        alias = f"f{n:02d}" + (".txt" if n % 3 == 0 else "")
        renamed[alias] = nm.split("/")[-1]
        b["name"] = DESK + alias
    elif nm.startswith(REAL_DESK):
        b["name"] = DESK + nm.split("/")[-1]
    out.append(b)
real = dict(d); real["itemAttrs"] = out
json.dump(real, open(os.path.join(OUT, "conf_real.json"), "w"), ensure_ascii=False, indent=1)
json.dump(renamed, open(os.path.join(OUT, "rename_map.json"), "w"), ensure_ascii=False, indent=1)

# 桌面项 → 网格 (C 套) 对照
C = dict(x0=120, y0=40, cw=113, rh=148, cols=22, rows=9)
desk = [a for a in out if str(a["name"]).startswith(DESK)]
items = [{"name": a["name"].split("/")[-1],
          "col": round((a["x"] - C["x0"]) / C["cw"]) + 1,
          "row": round((a["y"] - C["y0"]) / C["rh"]) + 1} for a in desk]
json.dump(items, open(os.path.join(OUT, "items_real.json"), "w"), ensure_ascii=False, indent=1)

# ---- 合成 A/B 套：坐标严格按套参数（真实网格），带真实屏幕字段 ----
def synth(spec, n_items, screen=1):
    x0, y0, cw, rh, cols, rows = spec
    its, cells = [], []
    c, r = 1, 1
    for i in range(n_items):
        while True:
            c += 1
            if c > cols - 5:      # 避开右锚 5 列（VIP/固定区）
                c = 1; r += 1
            if r > rows: break
            if r <= 4 and c <= 8: continue
            break
        cells.append((c, r))
        its.append({"name": DESK + f"a{i:02d}", "x": x0 + (c - 1) * cw, "y": y0 + (r - 1) * rh,
                    "z": 0, "screen": str(screen), "screenWidth": 1710 if spec is SPEC_A else 1728,
                    "screenHeight": 1112})
    return its

SPEC_A = (80, 44, 79, 114, 21, 9)
SPEC_B = (53, 43, 90, 114, 19, 9)
for name, spec, w, hsp in (("conf_a.json", SPEC_A, 1710, 11), ("conf_b.json", SPEC_B, 1728, 22)):
    its = synth(spec, 30)
    conf = {"lod": 64, "itemHSpacing": hsp, "itemVSpacing": 0, "fontSize": 11, "snapToGrid": True,
            "labelPosition": 1, "useStacks": False, "previewIcons": True, "visible": True,
            "itemAttrs": its}
    json.dump(conf, open(os.path.join(OUT, name), "w"), ensure_ascii=False, indent=1)

print(f"夹具输出 {OUT}: conf_real({len(out)} 项/桌面 {len(desk)}) items_real={len(items)} "
      f"conf_a/b(30 项) 卷宗={len(out)-len(desk)}")
print("VIP 检查:", [(a["name"], a["x"], a["y"]) for a in out if a["name"] in ("/", "SynologyDrive") or a["name"].startswith("/Volumes/Jon113")])
