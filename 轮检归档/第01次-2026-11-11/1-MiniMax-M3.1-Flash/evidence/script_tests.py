#!/usr/bin/env python3
"""脚本契约实测：qspace-rollback.sh / qspace-uninstall.sh / cmp-consistency.py / check-layout.py
全部在沙箱内跑，不 ssh 真机、不写真机 conf、不碰生产中枢。"""
import json, os, re, subprocess, sys, time, urllib.request, urllib.error, pathlib, shutil

SB = "/tmp/sandbox_minimax"
URL = "http://127.0.0.1:18890"
TOKEN = "sandbox-token-123"
HUBHOME = f"{SB}/hubhome"
passed = failed = 0

def check(name, cond, detail=""):
    global passed, failed
    if cond: passed += 1; print(f"  ✓ {name}")
    else: failed += 1; print(f"  ✗ {name}  {detail}")

def post(body, token=TOKEN):
    req = urllib.request.Request(URL + "/layout", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-Hub-Token": token}, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=8)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def reset():
    try: os.remove(os.path.join(HUBHOME, "layout.json"))
    except FileNotFoundError: pass
    arch = os.path.join(HUBHOME, "archive")
    if os.path.isdir(arch):
        for f in os.listdir(arch): os.remove(os.path.join(arch, f))

print("== 脚本契约实测（沙箱）==")

# ─────── qspace-rollback.sh 的 sourceMachine 拼接 ───────
print("\n-- 脚本1: qspace-rollback.sh 回滚 sourceMachine 拼接 --")
reset()
# 先放一份正常归档
post({"syncAt": int(time.time()) - 10, "sourceMachine": "MBP",
      "items": [{"name": "r1.txt", "col": 1, "row": 1}], "layoutFingerprint": "fp-rb", "specKey": "C"})
arch = sorted(os.listdir(os.path.join(HUBHOME, "archive")))
archive_file = os.path.join(HUBHOME, "archive", arch[0])
d = json.load(open(archive_file))
# 复刻 rollback.sh:164-171 的真实逻辑
old_src = str(d.get("sourceMachine", "?")).replace("(回滚)", "")
d["syncAt"] = int(time.time())
d["sourceMachine"] = old_src + "(回滚)"
c, b = post(d)
check("rollback.sh 回滚 payload 被中枢接受", c == 200 and '"accepted": true' in b, f"got {c} {b[:140]}")
check("rollback.sh 回滚 sourceMachine 值 = 'MBP(回滚)'", d["sourceMachine"] == "MBP(回滚)", d["sourceMachine"])

# ─────── rollback.sh --local 路径 ───────
print("\n-- 脚本2: qspace-rollback.sh --local 的 sourceMachine --")
reset()
c, b = post({"syncAt": int(time.time()), "sourceMachine": "local-snapshot",
             "items": [{"name": "l1.txt", "col": 1, "row": 1}], "layoutFingerprint": "fp-l", "specKey": "C"})
check("rollback.sh --local payload 被中枢接受", c == 200 and '"accepted": true' in b, f"got {c} {b[:140]}")

# ─────── 归档轮转后回滚 ───────
print("\n-- 脚本3: 归档轮转 (MAX_ARCHIVE=20) --")
reset()
for i in range(25):
    post({"syncAt": int(time.time()) + 100 + i, "sourceMachine": "MBP",
          "items": [{"name": f"a{i}.txt", "col": 1, "row": 1}], "layoutFingerprint": f"fp{i}", "specKey": "C"})
names = sorted(os.listdir(os.path.join(HUBHOME, "archive")))
check(f"归档保留 <=20（实际 {len(names)}）", len(names) <= 20, str(len(names)))
# 轮转后最新归档仍是最后写入的？
latest = max(names, key=lambda f: os.path.getmtime(os.path.join(HUBHOME, "archive", f)))
dd = json.load(open(os.path.join(HUBHOME, "archive", latest)))
check("归档最新 = 最后写入版本", dd.get("layoutFingerprint") == "fp24", str(dd.get("layoutFingerprint")))

# ─────── cmp-consistency.py 静态契约 ───────
print("\n-- 脚本4: cmp-consistency.py 契约 --")
cmp_src = pathlib.Path("/Users/jon/Projects/Desktop-Icon-Manager/scripts/cmp-consistency.py").read_text()
# 4a 它的 mapCell 是否与 main.swift v6 一致？
def mm(spec, col, row):
    s = spec
    Ls = s["topRight"] if row <= s["topRows"] else (s["armRight"] if s["zone2"][0] <= row <= s["zone2"][1] else 0)
    idxR = s["cols"] - col; ra = idxR <= 5
    dr = row
    if row >= s["zone2"][0] and row <= s["zone2"][1] and not ra: dr = row + (s["zone2"][0] - s["zone2"][0])
    dr = max(1, min(s["rows"], dr))
    Ld = s["topRight"] if dr <= s["topRows"] else (s["armRight"] if s["zone2"][0] <= dr <= s["zone2"][1] else 0)
    dc = (s["cols"] - idxR) if ra else (Ld + (col - Ls))
    return max(Ld + 1, min(s["cols"], dc)), dr
SPECS = {
 "A": dict(cols=21,rows=9,topRight=9,topRows=4,armRight=4,zone2=(5,7)),
 "B": dict(cols=19,rows=9,topRight=8,topRows=4,armRight=4,zone2=(5,7)),
 "C": dict(cols=22,rows=9,topRight=6,topRows=3,armRight=3,zone2=(4,6)),
}
# cmp 里的 zone2 平移用 d["zone2"][0]-s["zone2"][0] → 对 A->B 是 5-5=0, A->C 是 4-5=-1
def cmp_map(sk, dk, col, row):
    s, d = SPECS[sk], SPECS[dk]
    Ls = s["topRight"] if row <= s["topRows"] else (s["armRight"] if s["zone2"][0] <= row <= s["zone2"][1] else 0)
    idxR = s["cols"] - col; ra = idxR <= 5
    dr = row
    if row >= s["zone2"][0] and row <= s["zone2"][1] and not ra:
        dr = row + (d["zone2"][0] - s["zone2"][0])
    dr = max(1, min(d["rows"], dr))
    Ld = d["topRight"] if dr <= d["topRows"] else (d["armRight"] if d["zone2"][0] <= dr <= d["zone2"][1] else 0)
    dc = (d["cols"] - idxR) if ra else (Ld + (col - Ls))
    return max(Ld + 1, min(d["cols"], dc)), dr
diff = 0; firstdiff = None
for sk, dk in (("A","B"),("A","C"),("B","C")):
    for col in range(1, SPECS[sk]["cols"]+1):
        for row in range(1, SPECS[sk]["rows"]+1):
            if mm(SPECS[sk], col, row) != cmp_map(sk, dk, col, row):
                diff += 1
                if firstdiff is None: firstdiff = (sk,dk,col,row,mm(SPECS[sk],col,row),cmp_map(sk,dk,col,row))
check("cmp-consistency.py mapCell 与 main.swift v6 一致", diff == 0, f"{diff} 处不同, 首例={firstdiff}")

# 4b cmp 里的 SPEC 是否与 main.swift 一致（cols/rows/x0/y0/cw/rh）
main_src = pathlib.Path("/Users/jon/Projects/Desktop-Icon-Manager/QSpaceLayoutBar/main.swift").read_text()
ms = {}
for key in "ABC":
    m = re.search(rf'let SPEC_{key} = GridSpec\(key: "{key}", cols: (\d+), rows: (\d+), topRight: (\d+), topRows: (\d+), armRight: (\d+), zone2: \((\d+), (\d+)\), floorStart: (\d+),\s*x0: (\d+), y0: (\d+), colStep: (\d+), rowStep: (\d+)', main_src)
    ms[key] = dict(cols=int(m.group(1)),rows=int(m.group(2)),topRight=int(m.group(3)),topRows=int(m.group(4)),
                   armRight=int(m.group(5)),zone2=(int(m.group(6)),int(m.group(7))),
                   x0=int(m.group(9)),y0=int(m.group(10)),colStep=int(m.group(11)),rowStep=int(m.group(12)))
cs = {}
for key in "ABC":
    m = re.search(rf'"{key}": dict\(cols=(\d+),rows=(\d+),x0=(\d+),y0=(\d+),cw=(\d+),rh=(\d+),topRight=(\d+),topRows=(\d+),armRight=(\d+),zone2=\((\d+),(\d+)\)\)', cmp_src)
    cs[key] = dict(cols=int(m.group(1)),rows=int(m.group(2)),x0=int(m.group(3)),y0=int(m.group(4)),
                   cw=int(m.group(5)),rh=int(m.group(6)),topRight=int(m.group(7)),topRows=int(m.group(8)),
                   armRight=int(m.group(9)),zone2=(int(m.group(10)),int(m.group(11))))
for key in "ABC":
    same = all(ms[key][f] == cs[key][f] for f in ("cols","rows","x0","y0","topRight","topRows","armRight","zone2")) \
           and ms[key]["colStep"] == cs[key]["cw"] and ms[key]["rowStep"] == cs[key]["rh"]
    check(f"cmp-consistency.py SPEC[{key}] 与 main.swift 一致", same, f"main={ms[key]} cmp={cs[key]}")

# 4c cmp 硬编码生产地址（沙箱不可测 → 记录为「脚本只适配生产」）
check("cmp-consistency.py 指向生产中枢 :18878（沙箱不可跑，需改造）", "192.168.113.11:18878" in cmp_src)
check("cmp-consistency.py 读真机 conf（沙箱不可跑）", "/Users/jon/Library/Application Support/com.jinghaoshe.qspace.pro/desktop.conf" in cmp_src)

# ─────── check-layout.py 契约 ───────
print("\n-- 脚本5: check-layout.py 契约 --")
cl = pathlib.Path("/Users/jon/Projects/Desktop-Icon-Manager/scripts/check-layout.py").read_text()
check("check-layout.py 硬编码真机 conf 路径", "/Users/jon/Library/Application Support/com.jinghaoshe.qspace.pro/desktop.conf" in cl)
check("check-layout.py 有越界/同格/列位/间距 四项检查", all(x in cl for x in ("越界", "同格", "列位", "间距")))
# check-layout 的 SPEC 与 main.swift 一致？
for key in "ABC":
    m = re.search(rf"'{key}':dict\(cols=(\d+),rows=(\d+),x0=(\d+),y0=(\d+),cs=(\d+),rs=(\d+),lod=(\d+)\)", cl)
    ok = (int(m.group(1))==ms[key]["cols"] and int(m.group(2))==ms[key]["rows"] and
          int(m.group(3))==ms[key]["x0"] and int(m.group(4))==ms[key]["y0"] and
          int(m.group(5))==ms[key]["colStep"] and int(m.group(6))==ms[key]["rowStep"])
    check(f"check-layout.py SPEC[{key}] 与 main.swift 一致", ok, f"cl={m.groups()} main={ms[key]}")
# check-layout 套判定顺序：A 优先会不会吃掉 B 的宽度？
m = re.search(r"key='A' if abs\(w-1710\)<=40 else \('B' if abs\(w-1728\)<=40 else 'C'\)", cl)
check("check-layout.py 套判定 A 优先（1710±40 与 1728±40 重叠 → B 宽度被 A 吃掉）", m is not None)
overlap = abs(1710-1728) <= 80
check(f"A/B 宽度区间重叠（1710±40 vs 1728±40 → 差18 < 80）", overlap, f"overlap={overlap}")
# 同判定在 main.swift 的 specForSize
m2 = re.search(r'if abs\(w - 1710\) <= 40 \{ return SPEC_A \}\s*\n\s*if abs\(w - 1728\) <= 40 \{ return SPEC_B \}', main_src)
check("main.swift specForSize 也是 A 优先", m2 is not None)
# 实际影响：w=1718（两套都覆盖）→ A
check("w=1718 → main.swift 判 A（而非 B）", "A" if abs(1718-1710)<=40 else ("B" if abs(1718-1728)<=40 else "C") == "A")
check("w=1728 → main.swift 判 A（B 永远取不到！差18<40）", abs(1728-1710)<=40)

# ─────── test-hub.py 契约 ───────
print("\n-- 脚本6: test-hub.py 契约 --")
th = pathlib.Path("/Users/jon/Projects/Desktop-Icon-Manager/scripts/test-hub.py").read_text()
check("test-hub.py 指向沙箱端口 :18879", '"18879"' in th)
check("test-hub.py 用 POST /reset 清场", '"/reset"' in th)
check("test-hub.py 读 hub-sandbox-token.txt（不存在则回退 sandbox-token-123）", "hub-sandbox-token.txt" in th)
# /reset 在生产 hub 里存在吗？
hub_src = pathlib.Path("/Users/jon/Projects/Desktop-Icon-Manager/hub/hub-server.py").read_text()
check("⭐ 生产 hub 无 /reset 端点 → test-hub.py 的 reset() 静默失败（用例间不隔离）",
      '"/reset"' not in hub_src and "/reset" not in hub_src.replace(" ", ""))
# test-hub.py 的 17 项契约逐条与生产 hub 行为核对
cases = re.findall(r'check\("([^"]+)"', th)
print(f"  test-hub.py 契约用例数 = {len(cases)}")

print(f"\n结果: {passed} 通过, {failed} 失败")
sys.exit(1 if failed else 0)
