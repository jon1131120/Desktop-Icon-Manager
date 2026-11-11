#!/usr/bin/env python3
"""三台 conf 与中枢的一致性对比 v2（2026-11-11 升级）

v1 盲区（2026-11-11 实录）：只比文件名集合不比位置、不分屏面——
MBP 内置屏面的图标被用外接屏坐标解读，产出 9 项"假差异"，
而真正的位置漂移反而漏报。杰哥肉眼发现"桌面完全不一样"，脚本却说零差异。
v2 改为：主屏面 + 位置级（v6 归一后逐图标比对）+ 名单集合 三重比对。
"""
import json, os, shlex, subprocess, sys, urllib.request
from collections import defaultdict

TOKEN = open(os.path.expanduser("~/qspace-layouts/hub-token.txt")).read().strip()
CONF = "/Users/jon/Library/Application Support/com.jinghaoshe.qspace.pro/desktop.conf"
HUB = "http://192.168.113.11:18878"

SPEC = {
 "A": dict(cols=21,rows=9,x0=80,y0=44,cw=79,rh=114,topRight=9,topRows=4,armRight=4,zone2=(5,7)),
 "B": dict(cols=19,rows=9,x0=53,y0=43,cw=90,rh=114,topRight=8,topRows=4,armRight=4,zone2=(5,7)),
 "C": dict(cols=22,rows=9,x0=120,y0=40,cw=113,rh=148,topRight=6,topRows=3,armRight=3,zone2=(4,6)),
}
def leftBase(s,r):
    if r<=s["topRows"]: return s["topRight"]
    if s["zone2"][0]<=r<=s["zone2"][1]: return s["armRight"]
    return 0
def mapCell(sk,dk,col,row):
    s,d=SPEC[sk],SPEC[dk]
    Ls=leftBase(s,row); idxR=s["cols"]-col; ra=idxR<=5
    dr=row
    if row>=s["zone2"][0] and row<=s["zone2"][1] and not ra:
        dr=row+(d["zone2"][0]-s["zone2"][0])
    dr=max(1,min(d["rows"],dr))
    Ld=leftBase(d,dr)
    dc=(d["cols"]-idxR) if ra else (Ld+(col-Ls))
    return (max(Ld+1,min(d["cols"],dc)),dr)

def read_by_screen(h, spec):
    if h=="mbp":
        d=json.load(open(CONF))
    else:
        r=subprocess.run(["ssh","-o","BatchMode=yes",h,f"cat {shlex.quote(CONF)}"],
                         capture_output=True,text=True,timeout=30)
        d=json.loads(r.stdout)
    s=SPEC[spec]; out=defaultdict(dict)
    for a in d.get("itemAttrs",[]):
        nm=str(a.get("name",""))
        if not nm.startswith("/Users/jon/Desktop/"): continue
        x,y=a.get("x"),a.get("y")
        if x is None: continue
        c=max(1,min(s["cols"],round((x-s["x0"])/s["cw"])+1))
        r=max(1,min(s["rows"],round((y-s["y0"])/s["rh"])+1))
        out[str(a.get("screen","?"))][nm.split("/")[-1]]=(c,r)
    return dict(out)

req=urllib.request.Request(HUB+"/layout", headers={"X-Hub-Token":TOKEN})
hub=json.loads(urllib.request.urlopen(req,timeout=8).read().decode())
hubspec=hub.get("specKey","A")
hubitems={i["name"]:(i["col"],i["row"]) for i in hub["items"]}
print(f"中枢: {len(hubitems)} 项 spec={hubspec} source={hub.get('sourceMachine')}")

refs={"A":("air","A"),"B":("air","A"),"C":("mbp","C")}
fails=0
for h,spec in [("mbp","C"),("air","A"),("studio","C")]:
    try:
        scr=read_by_screen(h,spec)
    except Exception as e:
        print(f"  {h}: 读取失败 {e}"); fails+=1; continue
    scr_map = scr
    main_scr = max(scr, key=lambda k: len(scr[k])) if scr else ""
    main = scr.get(main_scr, {})
    mine=set(main); hubn=set(hubitems)
    mine -= {"SynologyDrive"}   # VIP 桌面项不进中枢快照（2026-11-12 杰哥定规），名单比对排除
    other_screens=set()
    for scr,items in scr.items() if False else scr_map.items():
        if scr != main_scr: other_screens |= set(items)
    only_l, only_h = sorted(mine-hubn), sorted(hubn-mine)
    second_screen = sorted(n for n in only_h if n in other_screens)   # 中枢多的项在本机第二屏面 = 正常（多屏桌面，中枢只同步主屏）
    truly_missing = sorted(n for n in only_h if n not in other_screens)  # 整机都没有 = iCloud 未到货
    pos_diff=[]
    for nm in mine & hubn:
        c,r=main[nm]
        if mapCell(spec,hubspec,c,r)!=hubitems[nm]:
            pos_diff.append((nm,(c,r),mapCell(spec,hubspec,c,r),hubitems[nm]))
    hard = bool(only_l or truly_missing or pos_diff)
    status = "✓" if not hard else "✗"
    extra = f" | 第二屏面 {len(second_screen)} 项（正常）" if second_screen else ""
    print(f"  {h}: {status} 本机主屏 {len(main)} 项 | 名单差 本机{len(only_l)}/中枢缺{len(truly_missing)} | 位置差 {len(pos_diff)}{extra}")
    for d in pos_diff[:5]:
        print(f"      {d[0][:26]:28s} {h}{d[1]} → 归一{d[2]}  中枢{d[3]}")
    for n in truly_missing[:5]:
        print(f"      中枢有本机整机无: {n[:40]}")
    if hard: fails+=1

sys.exit(1 if fails else 0)
