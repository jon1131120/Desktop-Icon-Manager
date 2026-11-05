#!/usr/bin/env python3
"""第八次审计 - 三台 conf 与中枢的数据一致性对比"""
import json, os, shlex, subprocess, sys, urllib.request

TOKEN = open(os.path.expanduser("~/qspace-layouts/hub-token.txt")).read().strip()
CONF = "/Users/jon/Library/Application Support/com.jinghaoshe.qspace.pro/desktop.conf"
HUB = "http://192.168.113.11:18878"

req = urllib.request.Request(HUB + "/layout", headers={"X-Hub-Token": TOKEN})
hub = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
hubitems = {i["name"]: (i["col"], i["row"]) for i in hub["items"]}
print(f"中枢 items: {len(hubitems)}")

hosts = ["mbp", "air", "studio"]
for h in hosts:
    if h == "mbp":
        d = json.load(open(CONF))
    else:
        # 路径含空格（"Application Support"），必须整体 quote 后交给远端 shell，
        # 不能用 ["ssh", ..., "cat", CONF]（会被拆成多个参数 → cat 报 Is a directory）
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", h, f"cat {shlex.quote(CONF)}"],
            capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(f"  {h}: 读取失败 {r.stderr[:80]}")
            continue
        try:
            d = json.loads(r.stdout)
        except Exception as e:
            print(f"  {h}: JSON 解析失败 {e}")
            continue
    items = [a for a in d.get("itemAttrs", []) if str(a.get("name", "")).startswith("/Users/jon/Desktop/")]
    mine = {}
    for a in items:
        nm = a["name"].split("/")[-1]
        x, y = a.get("x"), a.get("y")
        mine[nm] = (x, y)
    only_hub = sorted(set(hubitems) - set(mine))
    only_mine = sorted(set(mine) - set(hubitems))
    # 坐标一致性（按格换算比较）
    print(f"  {h}: 本机桌面 {len(mine)} | 中枢独有 {only_hub or '无'} | 本机独有 {only_mine or '无'}")
    print(f"       conf 键 {len(d.keys())} | itemAttrs 总 {len(d.get('itemAttrs', []))}")
