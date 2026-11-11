
import json,sys
from collections import Counter, defaultdict
conf=json.load(open('/Users/jon/Library/Application Support/com.jinghaoshe.qspace.pro/desktop.conf'))
SPECS={'A':dict(cols=21,rows=9,x0=80,y0=44,cs=79,rs=114,lod=64),
       'B':dict(cols=19,rows=9,x0=53,y0=43,cs=90,rs=114,lod=64),
       'C':dict(cols=22,rows=9,x0=120,y0=40,cs=113,rs=148,lod=96)}
grp=Counter()
for a in conf['itemAttrs']:
    grp[(str(a.get('screen',1)),a.get('screenWidth'),a.get('screenHeight'))]+=1
sid,w,h=max(grp.items(), key=lambda kv: kv[1])[0]
key=('A' if abs(w-1710)<=abs(w-1728) else 'B') if (abs(w-1710)<=40 and abs(w-1728)<=40) else ('A' if abs(w-1710)<=40 else ('B' if abs(w-1728)<=40 else 'C'))
sp=SPECS[key]
desk=[a for a in conf['itemAttrs'] if str(a.get('screen',1))==sid and str(a.get('name','')).startswith('/Users/jon/Desktop/')]
fails=[]
# 1 越界
for a in desk:
    if a['x'] + sp['lod']//2 > w: fails.append(f"越界 x={a['x']} {a['name'].split('/')[-1]}")
    if a['y'] + sp['lod']//2 > h: fails.append(f"越界 y={a['y']} {a['name'].split('/')[-1]}")
# 2 同格重复
cells=defaultdict(list)
for a in desk:
    c=round((a['x']-sp['x0'])/sp['cs'])+1; r=round((a['y']-sp['y0'])/sp['rs'])+1
    cells[(c,r)].append(a['name'].split('/')[-1])
dup={k:v for k,v in cells.items() if len(v)>1}
for k,v in dup.items(): fails.append(f"同格重叠 {k}: {v}")
# 3 列位不标准（偏差>3px）
for a in desk:
    c=round((a['x']-sp['x0'])/sp['cs'])+1
    std=sp['x0']+(c-1)*sp['cs']
    if abs(a['x']-std)>3: fails.append(f"列位偏移 {a['x']} vs 标准 {std} ({a['name'].split('/')[-1]})")
# 4 行内间距 < 图标宽（视觉重叠）
byrow=defaultdict(list)
for a in desk:
    byrow[round((a['y']-sp['y0'])/sp['rs'])+1].append(a)
for r,items in byrow.items():
    items.sort(key=lambda z:z['x'])
    for i in range(len(items)-1):
        gap=items[i+1]['x']-items[i]['x']
        if gap < sp['lod']: fails.append(f"行{r} 间距 {gap} < 图标宽 {sp['lod']} ({items[i]['name'].split('/')[-1]})")
print(f"套={key} 屏幕={w}x{h} lod={conf.get('lod')} 图标={len(desk)} 格={len(cells)}")
if fails:
    print(f"FAIL {len(fails)} 项:")
    for f in fails[:12]: print("  -",f)
else:
    print("PASS 无越界/无同格重叠/列位标准/行距足够")
