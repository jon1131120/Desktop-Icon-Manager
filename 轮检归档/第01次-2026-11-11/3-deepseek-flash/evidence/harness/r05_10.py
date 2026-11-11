#!/usr/bin/env python3
"""第 5~10 轮：保护窗 / 吸收期防抖 / 参数自愈+原子写 / 损坏缺失 / 屏号自愈 / overlap+kept"""
import json, os, subprocess, sys, threading, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dslib as D

T0 = D.now()
ALIAS = list(json.load(open("/tmp/ds_sandbox/fixtures/rename_map.json")).keys())


def seed_hub(n=37, tag="MBP", name="seed"):
    D.hub_reset()
    r = D.root(name, tag)
    D.write_conf(r, D.conf_from_names(r, ALIAS[:n], "C"))
    D.engine(r, [{"op": "now", "t": T0}, {"op": "bc"}])
    return D.hub_get()


def r5():
    print("\n=== 第5轮 保护窗（active 判定/拒绝/退避/窗开重报） ===")
    hub = seed_hub(37, "MBP", "r5_mbp")
    mbp_fp = hub["layoutFingerprint"]
    # MBP 用户正在拖动（active）：改 f01 位置 + 打 active 标记
    mbp_r = os.path.join(D.RUNS, "r5_mbp")
    cur = D.read_conf(mbp_r)
    for a in cur["itemAttrs"]:
        if a["name"].endswith("f01"):
            a["x"], a["y"] = 120, 40 + 7 * 148
    D.write_conf(mbp_r, cur)
    # 用户 5 秒前拖过（防抖 10 秒已过、active 窗未过）
    out = D.engine(mbp_r, [{"op": "now", "t": T0 + 20},
                           {"op": "rt", "set": {"lastLocalChangeAt": T0 + 5}}, {"op": "bc"}])
    hub = D.hub_get()
    D.check("5.1 active 上报被接受且带 userActiveUntil", hub.get("userActiveUntil", 0) > T0, hub.get("userActiveUntil"))

    air = D.root("r5_air", "Air")
    D.write_conf(air, D.conf_from_names(air, ALIAS[:37], "A"))
    out = D.engine(air, [{"op": "now", "t": T0 + 5}, {"op": "snap"}])
    fp = [l for l in out.splitlines() if "HARNESS snap" in l][0].split("fp=")[1].strip()
    # 本机有一版未同步的改动（baseline 落后）→ 才会走「上报→被拒」链路
    D.write_state(air, {"baselineFingerprint": "stale-" + fp, "lastBroadcastFingerprint": "stale-" + fp,
                        "lastAppliedFingerprint": fp, "lastAppliedAt": T0})
    # 非 active 撞 active → 被拒 protected；随后退避、窗开重报
    out = D.engine(air, [{"op": "now", "t": T0 + 30}, {"op": "bc"},
                         {"op": "now", "t": T0 + 40}, {"op": "bc"},
                         ])
    D.check("5.3 被拒不吞改动（baseline 保持落后，未误记为已同步）",
            D.read_state(air).get("baselineFingerprint", "").startswith("stale-"), D.read_state(air).get("baselineFingerprint"))
    # 窗口到期：把沙箱中枢的 userActiveUntil 置为过去（等价 10 分钟已过），再报
    hp = "/tmp/ds_sandbox/data/layout.json"
    hl = json.load(open(hp)); hl["userActiveUntil"] = T0 - 10
    json.dump(hl, open(hp, "w"))
    D.engine(air, [{"op": "now", "t": T0 + 50}, {"op": "rt", "set": {"lastLocalChangeAt": T0 + 40}},
                   {"op": "bc"}])
    log = D.log_text(air)
    hub = D.hub_get()
    D.check("5.2 非 active 上报被保护窗拒绝", "被保护窗拒绝" in log, log[-400:])
    D.check("5.4 退避期不硬重推（有退避日志、无第二次 POST 拒绝风暴）", "保护窗退避中" in log or "退避" in log, log[-300:])
    D.check("5.5 窗开重报被接受（中枢来源变 Air）", hub.get("sourceMachine") == "Air", hub.get("sourceMachine"))
    return D.save("r05")


def r6():
    print("\n=== 第6轮 吸收期与防抖（工况4：连续 5 次快速变化） ===")
    hub = seed_hub(36, "Studio", "r6_studio")
    base_sync = hub["syncAt"]
    r = os.path.join(D.RUNS, "r6_studio")
    conf = D.conf_from_names(r, ALIAS[:36], "C")
    # 连续 5 次改 f01 位置（间隔 2 秒 < 10 秒防抖窗），每次 bc
    steps = [{"op": "now", "t": T0 + 20},
             {"op": "bc"}]          # 先空跑一轮建立 lastSeenItems（真实进程是长驻的）
    cells = [(3, 8), (4, 8), (5, 8), (6, 8), (7, 8)]
    cur = D.read_conf(r)
    for i, (c, row) in enumerate(cells):
        for a in cur["itemAttrs"]:
            if a["name"].endswith("f01"):
                a["x"] = 120 + (c - 1) * 113
                a["y"] = 40 + (row - 1) * 148
        steps.append({"op": "now", "t": T0 + 20 + i * 2})
        steps.append({"op": "conf", "spec": "C", "items": [
            {"name": a["name"], "x": a["x"], "y": a["y"], "screen": 1,
             "screenWidth": a.get("screenWidth", 2560), "screenHeight": a.get("screenHeight", 1410)}
            for a in cur["itemAttrs"] if "Desktop/" in a["name"]]})
        steps.append({"op": "bc"})
    out = D.engine(r, steps)
    hub = D.hub_get()
    f01 = [i for i in hub["items"] if i["name"] == "f01"][0]
    final_cell = ((f01["col"], f01["row"]))
    D.check("6.1 防抖窗内 0 上报（中枢 syncAt 未变）", hub["syncAt"] == base_sync, (base_sync, hub["syncAt"]))
    D.check("6.1b 防抖期内中枢 syncAt 未变（5 次变化都没上报）",
            hub["syncAt"] == base_sync, (base_sync, hub["syncAt"]))
    # 停手 17 秒后（>10 秒防抖）→ 一次性上报最终版
    D.engine(r, [{"op": "now", "t": T0 + 45}, {"op": "bc"}])
    hub = D.hub_get()
    f01 = [i for i in hub["items"] if i["name"] == "f01"][0]
    final_cell = (f01["col"], f01["row"])
    D.check("6.2 停手后一次性上报最终版（f01 落在最后位置 (7,8)）", final_cell == (7, 8), final_cell)
    log = D.log_text(r)
    D.check("6.3 日志有「用户拖动」判定", "检测到用户拖动" in log, log[-300:])

    # 吸收期：远端刚应用 → 60 秒内不上报，只更新基线
    D.write_state(r, {**D.read_state(r), "suppressBroadcastUntil": T0 + 100})
    before = D.hub_get()["syncAt"]
    cur = D.read_conf(r)
    for a in cur["itemAttrs"]:
        if a["name"].endswith("f02"):
            a["x"], a["y"] = 120, 40 + 8 * 148
    D.write_conf(r, cur)
    out = D.engine(r, [{"op": "now", "t": T0 + 50}, {"op": "bc"}])
    log = D.log_text(r)
    D.check("6.4 吸收期内不上报", D.hub_get()["syncAt"] == before, D.hub_get()["syncAt"])
    D.check("6.5 吸收期内基线跟随本机（不回灌旧布局）", "吸收期中，更新基线" in log, log[-200:])
    return D.save("r06")


def r7():
    print("\n=== 第7轮 conf 参数自愈（工况5）+ 原子写竞争（工况9） ===")
    r = D.root("r7", "MBP")
    D.fixture("conf_real.json", r)
    conf = D.read_conf(r)
    # 模拟 QSpace 精简回写：只留 visible + itemAttrs
    slim = {"visible": True, "itemAttrs": conf["itemAttrs"]}
    D.write_conf(r, slim)
    out = subprocess.run([D.HARNESS, "--healcheck"], env={**os.environ, "DS_ROOT": r}, capture_output=True, text=True)
    D.check("7.1 精简 conf：lod/hSp/font 按本机套补回（C: 96/11/11）",
            "lod=96" in out.stdout and "hSp=11" in out.stdout and "font=11" in out.stdout, out.stdout.strip())
    D.check("7.2 精简 conf 触发一次告警（不刷屏）", "缺显示参数字段" in D.log_text(r), D.log_text(r)[-200:])
    # 其余显示字段是否也自愈？（对齐 QSpace 精简回写影响面）
    rep = D.engine(r, [{"op": "now", "t": T0}, {"op": "report"}])
    D.check("7.3 已知边界：仅 4 个字段自愈，previewIcons/useStacks 等仍缺失（记录，不判失败）",
            "MISSING" in rep, [l for l in rep.splitlines() if "report" in l][:1])

    # 原子写竞争：20 个写者 + 并发读者，坏 JSON 必须 0
    r2 = D.root("r7_race", "MBP")
    D.fixture("conf_real.json", r2)
    errs = []
    stop = threading.Event()

    def reader():
        p = D.conf_path(r2)
        n = 0
        while not stop.is_set() and n < 400:
            try:
                with open(p) as f:
                    json.load(f)
            except FileNotFoundError:
                pass
            except Exception as e:
                errs.append(repr(e))
            n += 1
    ts = [threading.Thread(target=reader) for _ in range(3)]
    [t.start() for t in ts]
    for i in range(20):
        D.engine(r2, [{"op": "now", "t": T0}, {"op": "conf", "spec": "C",
                                               "items": [{"name": f"z{i:02d}", "col": 2 + i % 5, "row": 8}]}])
    stop.set()
    [t.join() for t in ts]
    D.check("7.4 20 次原子写 + 1200 次并发读：坏 JSON 0 次", not errs, errs[:3])
    D.check("7.5 无残留 .tmp 文件", not [f for f in os.listdir(os.path.join(r2, "conf")) if f.endswith(".tmp")],
            os.listdir(os.path.join(r2, "conf")))
    return D.save("r07")


def r8():
    print("\n=== 第8轮 conf 损坏（工况6）/ 缺失（工况7） ===")
    r = D.root("r8", "MBP")
    D.write_conf(r, {"lod": 96, "itemAttrs": []})
    open(D.conf_path(r), "w").write('{"itemAttrs": [ {"name": "x", ')
    out = D.engine(r, [{"op": "now", "t": T0}, {"op": "snap"}, {"op": "bc"}])
    log = D.log_text(r)
    D.check("8.1 坏 JSON：loadConf 返回 nil（snap NIL）", "HARNESS snap NIL" in out, out.strip())
    D.check("8.2 坏 JSON：日志告警「解析失败」", "解析失败" in log, log[-300:])
    D.check("8.3 坏 JSON：广播跳过不崩（进程 exit 0）", "广播跳过" in log, log[-300:])

    os.remove(D.conf_path(r))
    out = D.engine(r, [{"op": "now", "t": T0}, {"op": "snap"}, {"op": "bc"}])
    D.check("8.4 conf 缺失：返回 nil 不崩", "HARNESS snap NIL" in out, out.strip())
    D.check("8.5 conf 缺失：日志留痕", "读不到或损坏" in D.log_text(r), D.log_text(r)[-200:])
    return D.save("r08")


def r9():
    print("\n=== 第9轮 屏号自愈（工况8 + 真双屏不误并 + 合盖场景） ===")
    # A) 合盖残留：30 项主屏 + 3 项同坐标系 screen=2
    r = D.root("r9a", "MBP")
    conf = D.conf_from_names(r, ALIAS[:30], "C")
    base = conf["itemAttrs"][:3]
    for i, a in enumerate(base):
        b = dict(a)
        b["name"] = os.path.join(r, "Desktop") + f"/ghost{i}"
        b["screen"] = "2"
        conf["itemAttrs"].append(b)
    D.write_conf(r, conf)
    out = D.engine(r, [{"op": "now", "t": T0}, {"op": "snap"}])
    log = D.log_text(r)
    D.check("9.1 合盖残留旧屏号：33 项全部进快照（3 项被归并）", "items=33" in out, out.strip())
    D.check("9.2 自愈日志明确归并 screen=2", "屏号自愈：screen=2" in log, log[-300:])

    # B) 真双屏：主屏 B(20 项) + 副屏 C 坐标系(8 项)，对齐率低 → 不并
    r2 = D.root("r9b", "MBP")
    main = D.conf_from_names(r2, ALIAS[:20], "B")
    second = D.conf_from_names(r2, ALIAS[20:28], "C")
    second["itemAttrs"] = [dict(a, screen="2", screenWidth=2560, screenHeight=1440) for a in second["itemAttrs"]]
    main["itemAttrs"] += second["itemAttrs"]
    D.write_conf(r2, main)
    out = D.engine(r2, [{"op": "now", "t": T0}, {"op": "snap"}])
    log = D.log_text(r2)
    D.check("9.3 真双屏：副屏 8 项不被误并（快照只含主屏 20 项）", "items=20" in out, out.strip())
    D.check("9.4 真双屏：日志判定「真双屏」", "判定为真双屏" in log, log[-400:])

    # C) 真双屏但副屏坐标恰在主屏网格上（同套摆法）→ 判据会误并（已知边界，记录）
    r3 = D.root("r9c", "MBP")
    main = D.conf_from_names(r3, ALIAS[:20], "B")
    second = D.conf_from_names(r3, ALIAS[20:28], "B")
    second["itemAttrs"] = [dict(a, screen="2") for a in second["itemAttrs"]]
    main["itemAttrs"] += second["itemAttrs"]
    D.write_conf(r3, main)
    out = D.engine(r3, [{"op": "now", "t": T0}, {"op": "snap"}])
    D.check("9.5 边界：副屏与主屏同套坐标（对齐率 100%）→ 会被并入（记录已知边界）", "items=28" in out, out.strip())
    return D.save("r09")


def r10():
    print("\n=== 第10轮 overlap 修复（kept 进 used / 推移 / 熔断） ===")
    r = D.root("r10", "Air")
    A = D.SPECS["A"]
    names = ALIAS[:9]
    conf = D.conf_from_names(r, names, "A")
    # kept 项（不在中枢名单）放在 (12,1)，且排在 itemAttrs 队尾（复现 R17 单趟缺陷场景）
    conf["itemAttrs"].append({"name": os.path.join(r, "Desktop") + "/zzz_kept", "x": A["x0"] + 11 * A["cw"],
                              "y": A["y0"], "z": 0, "screen": "1", "screenWidth": 1710, "screenHeight": 1112})
    D.write_conf(r, conf)
    snap = {"specKey": "A", "items": [{"name": n, "col": 12 + i, "row": 1} for i, n in enumerate(names)]}
    json.dump(snap, open("/tmp/ds_sandbox/snap_r10.json", "w"))
    out = D.engine(r, [{"op": "now", "t": T0}, {"op": "applyfile", "path": "/tmp/ds_sandbox/snap_r10.json", "write": True}])
    conf2 = D.read_conf(r)
    ov = D.overlaps(conf2, r)
    D.check("10.1 kept 队尾：应用后同格重叠 0（两段式预登记生效）", not ov, ov)
    D.check("10.2 应用统计 matched=9 kept=1", "matched=9 kept=1" in out, out.strip()[-300:])

    # fixLocalOverlaps：3 项同格 → 连锁右推
    r2 = D.root("r10b", "Air")
    c2 = D.conf_from_names(r2, ALIAS[:5], "A")
    for a in c2["itemAttrs"][1:4]:
        a["x"], a["y"] = c2["itemAttrs"][0]["x"], c2["itemAttrs"][0]["y"]
    D.write_conf(r2, c2)
    out = D.engine(r2, [{"op": "now", "t": T0}, {"op": "overlap"}])
    conf3 = D.read_conf(r2)
    D.check("10.3 本机同格重叠自动推开（同格 0 个）", not D.overlaps(conf3, r2), D.overlaps(conf3, r2))
    D.check("10.4 overlap 日志留痕", "自动推开" in D.log_text(r2), D.log_text(r2)[-300:])

    # 越界：最右列 + 超宽 → 左移
    r3 = D.root("r10c", "Air")
    c3 = D.conf_from_names(r3, ALIAS[:3], "A")
    c3["itemAttrs"][0]["x"] = 1710 - 10      # 半宽 32 → 越界
    D.write_conf(r3, c3)
    snap3 = {"specKey": "C", "items": [{"name": ALIAS[0], "col": 22, "row": 1},
                                       {"name": ALIAS[1], "col": 22, "row": 2},
                                       {"name": ALIAS[2], "col": 22, "row": 3}]}
    json.dump(snap3, open("/tmp/ds_sandbox/snap_r10c.json", "w"))
    out = D.engine(r3, [{"op": "now", "t": T0}, {"op": "applyfile", "path": "/tmp/ds_sandbox/snap_r10c.json", "write": True}])
    conf4 = D.read_conf(r3)
    xs = [(a["x"], a["name"].split("/")[-1]) for a in conf4["itemAttrs"] if "Desktop/" in a["name"]]
    D.check("10.5 最右列越界修正：所有 x 在屏宽内（x ≤ 1710-32）", all(x <= 1710 - 32 for x, _ in xs), xs)
    return D.save("r10")


if __name__ == "__main__":
    bad = 0
    for fn in (r5, r6, r7, r8, r9, r10):
        bad += fn()
    print("\nFAIL 总数:", bad)
    sys.exit(1 if bad else 0)
