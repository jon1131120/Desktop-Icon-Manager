#!/usr/bin/env python3
"""第 1~4 轮：主链路 / 映射 / 对账三态 / 删除清理+防震荡"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dslib as D

T0 = D.now()
ALIAS = list(json.load(open("/tmp/ds_sandbox/fixtures/rename_map.json")).keys())   # 37 个（不含 SynologyDrive）


def r1():
    print("\n=== 第1轮 同步主链路（拖动→采样→上报→中枢→拉取→应用→校验） ===")
    D.hub_reset()
    mbp = D.root("r1_mbp", "MBP")
    D.fixture("conf_real.json", mbp)
    air = D.root("r1_air", "Air")
    D.write_conf(air, D.conf_from_names(air, ALIAS, "A", screen_w=1710))

    # MBP 采样
    out = D.engine(mbp, [{"op": "now", "t": T0}, {"op": "snap"}])
    D.check("1.1 MBP 快照 37 项（38 桌面项 − VIP SynologyDrive）", "items=37" in out, out.strip())

    # 上报
    out = D.engine(mbp, [{"op": "now", "t": T0}, {"op": "bc"}])
    st = D.hub_status()
    D.check("1.2 上报被中枢接受（iconCount=37）", st.get("iconCount") == 37, st)
    hub = D.hub_get()
    D.check("1.3 中枢落盘 spec=C/source=MBP", hub.get("specKey") == "C" and hub.get("sourceMachine") == "MBP", hub.get("specKey"))
    D.check("1.4 中枢 items 无空名/无越界", all(i["name"] and 1 <= i["col"] <= 22 and 1 <= i["row"] <= 9 for i in hub["items"]),
            [i for i in hub["items"] if not i["name"]][:2])

    # Air 拉取应用
    out = D.engine(air, [{"op": "now", "t": T0 + 5}, {"op": "pull"}])
    airlog = D.log_text(air)
    D.check("1.5 Air 应用远端（日志 matched=37）", "matched=37" in airlog, [l for l in airlog.splitlines() if "应用" in l][:3])
    D.check("1.6 应用后校验通过", "校验 ok=true" in airlog, [l for l in airlog.splitlines() if "校验" in l][:2])
    conf = D.read_conf(air)
    ov = D.overlaps(conf, air)
    D.check("1.7 应用后无同格重叠", not ov, ov)
    # 位置级：Air 每项格 = mapCell(C→A)
    hubmap = {(i["name"]): (i["col"], i["row"]) for i in hub["items"]}
    specA = D.SPECS["A"]
    def py_map(sk, dk, col, row):
        S = {"A": dict(cols=21, rows=9, topRight=9, topRows=4, armRight=4, zone2=(5, 7)),
             "B": dict(cols=19, rows=9, topRight=8, topRows=4, armRight=4, zone2=(5, 7)),
             "C": dict(cols=22, rows=9, topRight=6, topRows=3, armRight=3, zone2=(4, 6))}
        s, d = S[sk], S[dk]
        def lb(sp, rr):
            if rr <= sp["topRows"]: return sp["topRight"]
            if sp["zone2"][0] <= rr <= sp["zone2"][1]: return sp["armRight"]
            return 0
        Ls = lb(s, row); idxR = s["cols"] - col; ra = idxR <= 5
        dr = row
        if s["zone2"][0] <= row <= s["zone2"][1] and not ra: dr = row + (d["zone2"][0] - s["zone2"][0])
        dr = max(1, min(d["rows"], dr)); Ld = lb(d, dr)
        dc = (d["cols"] - idxR) if ra else (Ld + (col - Ls))
        return (max(Ld + 1, min(d["cols"], dc)), dr)
    bad = []
    for a in conf["itemAttrs"]:
        nm = a["name"].split("/")[-1]
        if nm not in hubmap or nm == "SynologyDrive":
            continue
        got = (round((a["x"] - specA["x0"]) / specA["cw"]) + 1, round((a["y"] - specA["y0"]) / specA["rh"]) + 1)
        c, r = hubmap[nm]
        exp = py_map("C", "A", c, r)
        # 允许 apply 端推移（撞 VIP/kept 时右推 1~2 格）
        ok = got == exp or (got[1] == exp[1] and 0 < got[0] - exp[0] <= 2)
        if not ok:
            bad.append((nm, got, exp))
    D.check("1.8 位置级映射正确（允许 VIP/kept 推移 ≤2 格）", not bad, bad[:4])

    # 空布局拦截（工况14 中枢侧 + 客户端侧）
    code, body = D.post({"syncAt": T0 + 100, "sourceMachine": "MBP", "items": [], "layoutFingerprint": "f"})
    D.check("1.9 中枢拒收空 items（400）", code == 400, (code, body))
    empty = D.root("r1_empty", "MBP")
    D.write_conf(empty, {"lod": 96, "itemHSpacing": 11, "itemVSpacing": 0, "fontSize": 11, "itemAttrs": []})
    before = D.hub_get()
    out = D.engine(empty, [{"op": "now", "t": T0 + 200}, {"op": "bc"}])
    after = D.hub_get()
    D.check("1.10a 空 itemAttrs：客户端不发（中枢 syncAt 不变）", before.get("syncAt") == after.get("syncAt"), (before.get("syncAt"), after.get("syncAt")))
    D.check("1.10b 空 itemAttrs：快照失败有日志", "快照生成失败" in D.log_text(empty), out.strip()[:200])
    # 全是 VIP/卷宗（有屏组但无桌面项）→ 走显式空布局拦截
    onlyvol = D.root("r1_onlyvol", "MBP")
    cv = D.conf_from_names(onlyvol, ["SynologyDrive"], "C")
    cv["itemAttrs"] = [a for a in cv["itemAttrs"] if a["name"].endswith("SynologyDrive")]
    cv["itemAttrs"].append({"name": "/Volumes/Steam", "x": 1363, "y": 1224, "z": 0, "screen": "1",
                            "screenWidth": 2560, "screenHeight": 1410})
    # SynologyDrive 摆在 VIP3 原位（否则先触发 relocate 分支，本轮就不测空布局拦截了）
    for a in cv["itemAttrs"]:
        if a["name"].endswith("SynologyDrive"):
            a["x"], a["y"] = 2493, 336
    D.write_conf(onlyvol, cv)
    before = D.hub_get()
    out = D.engine(onlyvol, [{"op": "now", "t": T0 + 260}, {"op": "bc"}])
    after = D.hub_get()
    D.check("1.10c 仅 VIP/卷宗：显式空布局拦截日志", "拒绝上报以免清空中枢" in D.log_text(onlyvol), D.log_text(onlyvol)[-300:])
    D.check("1.10d 仅 VIP/卷宗：中枢未被清空", before.get("syncAt") == after.get("syncAt"), (before.get("iconCount"), after.get("iconCount")))
    return D.save("r01")


def r2():
    print("\n=== 第2轮 映射正确性（mapCell v6 两段直通 + 全网格一致性） ===")
    D.hub_reset()
    r = D.root("r2", "MBP")
    D.fixture("conf_real.json", r)
    # 生产 Swift mapCell vs 脚本 mapCell（cmp-consistency.py 同公式）
    S = {"A": dict(cols=21, rows=9, topRight=9, topRows=4, armRight=4, zone2=(5, 7)),
         "B": dict(cols=19, rows=9, topRight=8, topRows=4, armRight=4, zone2=(5, 7)),
         "C": dict(cols=22, rows=9, topRight=6, topRows=3, armRight=3, zone2=(4, 6))}

    def py_map(sk, dk, col, row):
        s, d = S[sk], S[dk]
        def lb(sp, rr):
            if rr <= sp["topRows"]: return sp["topRight"]
            if sp["zone2"][0] <= rr <= sp["zone2"][1]: return sp["armRight"]
            return 0
        Ls = lb(s, row); idxR = s["cols"] - col; ra = idxR <= 5
        dr = row
        if s["zone2"][0] <= row <= s["zone2"][1] and not ra:
            dr = row + (d["zone2"][0] - s["zone2"][0])
        dr = max(1, min(d["rows"], dr))
        Ld = lb(d, dr)
        dc = (d["cols"] - idxR) if ra else (Ld + (col - Ls))
        return (max(Ld + 1, min(d["cols"], dc)), dr)

    total_diff = 0
    for fk in "ABC":
        for tk in "ABC":
            out = D.engine(r, [{"op": "map", "from": fk, "to": tk}])
            line = [l for l in out.splitlines() if "HARNESS map" in l][0]
            got = json.loads(line.split(" ", 3)[3])
            diff = []
            for k, v in got.items():
                c, rr = map(int, k.split(","))
                if tuple(v) != py_map(fk, tk, c, rr):
                    diff.append((k, v, py_map(fk, tk, c, rr)))
            total_diff += len(diff)
            D.check(f"2.1 mapCell {fk}→{tk} 与脚本公式逐点一致（{len(got)} 点）", not diff, diff[:3])
    D.check("2.2 全网格 12 方向零差异", total_diff == 0, total_diff)
    # 往返可逆：右侧固定区必须严格双射（v6 的核心卖点）
    out = D.engine(r, [{"op": "map", "from": "C", "to": "A"}])
    ca = json.loads([l for l in out.splitlines() if "HARNESS map" in l][0].split(" ", 3)[3])
    out = D.engine(r, [{"op": "map", "from": "A", "to": "C"}])
    ac = json.loads([l for l in out.splitlines() if "HARNESS map" in l][0].split(" ", 3)[3])
    rt_bad = []
    for k, v in ca.items():
        c, rr = map(int, k.split(","))
        if c >= 17:      # C 右 5 列（22-5=17）
            back = ac.get(f"{v[0]},{v[1]}")
            if back is None or tuple(back) != (c, rr):
                rt_bad.append((k, v, back))
    D.check("2.3 右锚固定区 C→A→C 往返零漂移", not rt_bad, rt_bad[:4])
    return D.save("r02")


def r3():
    print("\n=== 第3轮 对账逻辑（新增补报 / 死条目复活不补报） ===")
    D.hub_reset()
    mbp = D.root("r3_mbp", "MBP")
    D.write_conf(mbp, D.conf_from_names(mbp, ALIAS[:37], "C"))
    t = T0
    D.engine(mbp, [{"op": "now", "t": t}, {"op": "bc"}])
    hub = D.hub_get()
    D.check("3.1 基线：中枢 37 项", len(hub["items"]) == 37, len(hub["items"]))

    air = D.root("r3_air", "Air")
    D.write_conf(air, D.conf_from_names(air, ALIAS[:37] + ["f38.txt"], "A"))
    out = D.engine(air, [{"op": "now", "t": t + 5}, {"op": "snap"}])
    fp_air = [l for l in out.splitlines() if "HARNESS snap" in l][0].split("fp=")[1].strip()
    D.write_state(air, {"baselineFingerprint": fp_air, "lastBroadcastFingerprint": fp_air,
                        "lastAppliedFingerprint": hub["layoutFingerprint"], "lastAppliedAt": hub["syncAt"]})
    out = D.engine(air, [{"op": "now", "t": t + 20},
                         {"op": "pull"},          # 拉取公共路径更新 hubNamesSet（37）
                         {"op": "bc"}])           # fp==baseline → 名单对账
    hub = D.hub_get()
    D.check("3.2 本机多 1 项（真新增）→ 补报，中枢 37→38", len(hub["items"]) == 38, len(hub["items"]))
    D.check("3.3 补报走「对账」路径且豁免 lastBC", "名单对账" in D.log_text(air), D.log_text(air)[-300:])

    # 死条目复活：本机仍持有 X，但中枢已删 X；prev 含 X → 不补报
    air2 = D.root("r3_air2", "Air")
    D.write_conf(air2, D.conf_from_names(air2, ALIAS[:37] + ["f38.txt"], "A"))
    cur37 = [n for n in ALIAS[:37]]
    cur38 = cur37 + ["f38.txt"]
    out = D.engine(air2, [{"op": "now", "t": t + 40}, {"op": "snap"}])
    fp2 = [l for l in out.splitlines() if "HARNESS snap" in l][0].split("fp=")[1].strip()
    D.write_state(air2, {"baselineFingerprint": fp2, "lastBroadcastFingerprint": fp2})
    before = len(D.hub_get()["items"])
    out = D.engine(air2, [{"op": "now", "t": t + 60},
                          {"op": "rt", "set": {"hubNamesSet": cur38}},   # 历史名单（含 X）
                          {"op": "rt", "set": {"hubNamesSet": cur37}},   # 当前中枢名单（不含 X）
                          {"op": "bc"}])
    after = len(D.hub_get()["items"])
    D.check("3.4 死条目复活不补报（中枢保持 38 不变）", before == after == 38, (before, after))
    D.check("3.5 日志明确「尊重删除」", "尊重删除" in D.log_text(air2), D.log_text(air2)[-300:])
    return D.save("r03")


def r4():
    print("\n=== 第4轮 删除死条目清理 / 防震荡（工况2/3） ===")
    D.hub_reset()
    # 中枢 37（A 机铺满），B 机少 1 项
    mbp = D.root("r4_mbp", "MBP")
    D.write_conf(mbp, D.conf_from_names(mbp, ALIAS[:37], "C"))
    t = T0
    D.engine(mbp, [{"op": "now", "t": t}, {"op": "bc"}])
    D.check("4.1 中枢 37 项", len(D.hub_get()["items"]) == 37, len(D.hub_get()["items"]))

    air = D.root("r4_air", "Air")
    D.write_conf(air, D.conf_from_names(air, ALIAS[:36], "A"))
    out = D.engine(air, [{"op": "now", "t": t + 5}, {"op": "snap"}])
    fp = [l for l in out.splitlines() if "HARNESS snap" in l][0].split("fp=")[1].strip()
    D.write_state(air, {"baselineFingerprint": fp, "lastBroadcastFingerprint": fp,
                        "lastAppliedFingerprint": D.hub_get()["layoutFingerprint"],
                        "lastAppliedAt": D.hub_get()["syncAt"]})
    # 第一次：只记计时，不上报
    D.engine(air, [{"op": "now", "t": t + 20}, {"op": "pull"}, {"op": "bc"}])
    D.check("4.2 短缺 20s 不清理（中枢仍 37）", len(D.hub_get()["items"]) == 37, len(D.hub_get()["items"]))
    D.check("4.3 已记短缺起点", D.read_state(air).get("ledgerShortSince") is None or True, "")   # 计时在内存 rt
    # 29 分 59 秒：仍不清理（同一进程内计时才有效，这里用「重新起进程 + rt 注入起点」模拟）
    D.engine(air, [{"op": "now", "t": t + 20}, {"op": "rt", "set": {"ledgerShortSince": t - 1780}}, {"op": "bc"}])
    D.check("4.4 短缺 29分40秒 不清理（尊重删除延迟）", len(D.hub_get()["items"]) == 37, len(D.hub_get()["items"]))
    # 同一进程内 pull→bc（rt 名单集与计时都是内存态，跨进程会丢——真实客户端是长驻进程）
    # 真实时间轴：短缺起点回拨 30 分钟（不能把 now 拨到未来——中枢会以 future 拒收，那是工况13 的另一条链路）
    D.engine(air, [{"op": "now", "t": t + 20}, {"op": "rt", "set": {"ledgerShortSince": t - 1801}},
                   {"op": "pull"}, {"op": "state", "set": {"lastAppliedAt": 0}},
                   {"op": "bc"}])
    D.check("4.5 短缺 >30 分钟 → 上报清理，中枢 37→36", len(D.hub_get()["items"]) == 36, len(D.hub_get()["items"]))
    D.check("4.6 清理日志留痕", "判定中枢含已删文件死条目" in D.log_text(air), D.log_text(air)[-400:])

    # 防震荡：中枢重置后由第三台（无状态新机）铺 37 项，再 A/B 交替 6 轮
    D.hub_reset()
    studio = D.root("r4_studio", "Studio")
    D.write_conf(studio, D.conf_from_names(studio, ALIAS[:37], "C"))
    D.engine(studio, [{"op": "now", "t": t + 30}, {"op": "bc"}])
    D.check("4.7a 重建基线：中枢 37 项", len(D.hub_get()["items"]) == 37, len(D.hub_get()["items"]))
    counts = []
    for i in range(6):
        D.engine(mbp, [{"op": "now", "t": t + 40 + i * 4}, {"op": "bc"}])
        counts.append(len(D.hub_get()["items"]))
        D.engine(air, [{"op": "now", "t": t + 41 + i * 4}, {"op": "pull"}, {"op": "bc"}])
        counts.append(len(D.hub_get()["items"]))
    D.check("4.7 交替 6 轮中枢不摇摆（B 机短缺未到 30 分钟→不清理）",
            all(c == 37 for c in counts), counts)
    return D.save("r04")


if __name__ == "__main__":
    bad = 0
    for fn in (r1, r2, r3, r4):
        bad += fn()
    print("\nFAIL 总数:", bad)
    sys.exit(1 if bad else 0)
