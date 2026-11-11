#!/usr/bin/env python3
"""跨实现一致性：cmp-consistency.py 的 mapCell vs 生产 main.swift 的 mapCell（真实输出比对）"""
import pathlib, re, sys

SB = "/tmp/sandbox_minimax"
prod_file = pathlib.Path(f"{SB}/home/mapcell-prod.txt")
if not prod_file.exists():
    print("缺少生产 mapCell 输出，先跑 run_scenarios.sh 10"); sys.exit(2)
prod = {}
for line in prod_file.read_text().splitlines():
    k, v = line.rsplit("=", 1)
    pair, cr = k.split(":")
    sk, dk = pair.split(">")
    prod[(sk, dk, cr)] = v
print(f"生产 mapCell 输出条数 = {len(prod)}")

# 直接 import cmp-consistency.py 的 mapCell（它是脚本，读真机 conf 会失败 → 只取函数定义）
cmp_src = pathlib.Path("/Users/jon/Projects/Desktop-Icon-Manager/scripts/cmp-consistency.py").read_text()
ns = {}
exec(compile(cmp_src.split("def read_by_screen")[0], "cmp", "exec"), ns)
cmp_map = ns["mapCell"]

diff = []
for (sk, dk, cr), pv in prod.items():
    col, row = (int(x) for x in cr.split(","))
    cv = f"{cmp_map(sk, dk, col, row)[0]},{cmp_map(sk, dk, col, row)[1]}"
    if cv != pv:
        diff.append((sk, dk, col, row, pv, cv))

print(f"比对结果：{len(prod)} 条，差异 {len(diff)} 条")
for d in diff[:12]:
    print(f"  ✗ {d[0]}->{d[1]} ({d[2]},{d[3]}) 生产={d[4]} cmp={d[5]}")

# 差异归因：是不是 zone2 平移偏移量不同？
SPEC = ns["SPEC"]
def prod_map(sk, dk, col, row):
    """忠实复刻 main.swift:656-681"""
    s, d = SPEC[sk], SPEC[dk]
    Ls = ns["leftBase"](s, row)
    idxR = s["cols"] - col
    ra = idxR <= 5
    dr = row
    if row >= s["zone2"][0] and row <= s["zone2"][1] and not ra:
        dr = row + (d["zone2"][0] - s["zone2"][0])
    dr = max(1, min(d["rows"], dr))
    Ld = ns["leftBase"](d, dr)
    dc = (d["cols"] - idxR) if ra else (Ld + (col - Ls))
    dc = max(Ld + 1, min(d["cols"], dc))
    return dc, dr

d2 = 0
first = None
for (sk, dk, cr), pv in prod.items():
    col, row = (int(x) for x in cr.split(","))
    if prod_map(sk, dk, col, row) != tuple(int(x) for x in pv.split(",")):
        d2 += 1
        if first is None: first = (sk, dk, col, row, pv, prod_map(sk, dk, col, row))
print(f"\n用「忠实复刻 main.swift」的 Python 版再比：差异 {d2} 条  首例={first}")

# cmp 版 vs 忠实复刻版：定位 cmp 的真实差异点
d3 = 0
ex3 = []
for sk in SPEC:
    for dk in SPEC:
        if sk == dk: continue
        for col in range(1, SPEC[sk]["cols"] + 1):
            for row in range(1, SPEC[sk]["rows"] + 1):
                a = cmp_map(sk, dk, col, row)
                b = prod_map(sk, dk, col, row)
                if a != b:
                    d3 += 1
                    if len(ex3) < 6: ex3.append((sk, dk, col, row, a, b))
print(f"cmp 版 vs 忠实复刻版：差异 {d3} 条")
for e in ex3: print(f"    {e[0]}->{e[1]} ({e[2]},{e[3]}) cmp={e[4]} 复刻={e[5]}")
sys.exit(0 if len(diff) == 0 else 1)
