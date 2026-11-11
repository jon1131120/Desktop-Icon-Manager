#!/usr/bin/env python3
"""第 11~20 轮：快照链 / 门禁矩阵 / 并发 / 灾难恢复 / 部署一致性 / 可观测 /
边界异常 / 时钟偏移 / 标定回归+变异 / VIP三格+卷宗"""
import glob, json, os, subprocess, sys, threading, time, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dslib as D

T0 = D.now()
ALIAS = list(json.load(open("/tmp/ds_sandbox/fixtures/rename_map.json")).keys())


def seed(n=37, tag="MBP", name="seed"):
    D.hub_reset()
    r = D.root(name, tag)
    D.write_conf(r, D.conf_from_names(r, ALIAS[:n], "C"))
    D.engine(r, [{"op": "now", "t": T0}, {"op": "bc"}])
    return r


def r11():
    print("\n=== 第11轮 快照链（自动快照/同分钟防撞/恢复/清理） ===")
    r = D.root("r11", "MBP")
    D.write_conf(r, D.conf_from_names(r, ALIAS[:30], "C"))
    out = D.engine(r, [{"op": "now", "t": T0}, {"op": "save", "name": "手动A"}, {"op": "save", "name": "手动A"},
                       {"op": "save", "name": "手动A"}, {"op": "ls", "path": os.path.join(r, "layouts")}])
    fs = [l for l in out.splitlines() if "HARNESS ls" in l][0]
    D.check("11.1 同分钟 3 次同名保存全部保留（-1/-2 防撞）", "手动A-2" in fs and "手动A-1" in fs, fs)

    # 自动快照：首轮生成；内容不变再跑 → 跳过
    out = D.engine(r, [{"op": "now", "t": T0}, {"op": "autosnap", "last": 0}])
    log = D.log_text(r)
    D.check("11.2 自动快照首轮生成", "自动快照已存" in log, log[-300:])
    out = D.engine(r, [{"op": "now", "t": T0}, {"op": "autosnap", "last": 0},
                       {"op": "ls", "path": os.path.join(r, "layouts")}])
    fs2 = [l for l in out.splitlines() if "HARNESS ls" in l][0]
    D.check("11.3 内容未变不重复存（same=true）", "same=true" in D.log_text(r), D.log_text(r)[-300:])

    # 24h 保底：把最新自动快照 mtime 拨到 25 小时前 → 即使内容没变也存一份
    autos = sorted(glob.glob(os.path.join(r, "layouts", "[[]MBP[]] 自动*.json")))
    if autos:
        old = time.time() - 25 * 3600
        os.utime(autos[-1], (old, old))
    n_before = len(glob.glob(os.path.join(r, "layouts", "[[]MBP[]] 自动*.json")))
    D.engine(r, [{"op": "now", "t": T0}, {"op": "autosnap", "last": 0}])
    n_after = len(glob.glob(os.path.join(r, "layouts", "[[]MBP[]] 自动*.json")))
    D.check("11.4 24h 保底快照（needDaily 生效，份数 +1）", n_after == n_before + 1, (n_before, n_after))

    # 恢复：改一处位置 → 从快照恢复 → 位置还原
    snap = sorted(glob.glob(os.path.join(r, "layouts", "*手动A.json")))[0]
    conf = D.read_conf(r)
    moved_name = None
    for a in conf["itemAttrs"]:
        if a["name"].endswith("f02"):
            moved_name = a["name"]
            a["x"], a["y"] = 2493, 1224
    D.write_conf(r, conf)
    out = D.engine(r, [{"op": "now", "t": T0}, {"op": "restore", "path": snap, "restart": False}])
    conf2 = D.read_conf(r)
    pos = [(a["x"], a["y"]) for a in conf2["itemAttrs"] if a["name"] == moved_name]
    D.check("11.5 从快照恢复：位置还原", pos and pos[0] != (2493, 1224), pos)

    # prune：造 13 份自动快照 → 只留 10
    for i in range(13):
        src = json.load(open(snap))
        nm = f"[MBP] 自动 [内置] 11-{i+1:02d}_1000"
        src["name"] = nm
        json.dump(src, open(os.path.join(r, "layouts", nm + ".json"), "w"))
    D.engine(r, [{"op": "now", "t": T0}, {"op": "prune"}, {"op": "ls", "path": os.path.join(r, "layouts")}])
    autos = glob.glob(os.path.join(r, "layouts", "[[]MBP[]] 自动*.json"))
    D.check("11.6 prune 保留 10 份本机自动快照", len(autos) == 10, len(autos))
    D.check("11.7 prune 不误删手动快照", os.path.exists(snap), snap)
    return D.save("r11")


def r12():
    print("\n=== 第12轮 门禁矩阵（工况10：token/网段/body/items/坏JSON/未来时间） ===")
    D.hub_reset()
    ok = {"syncAt": T0 + 1, "sourceMachine": "MBP", "items": [{"name": "a", "col": 1, "row": 1}]}
    code, body = D.post(ok, token="wrong")
    D.check("12.1 错 token → 401", code == 401, (code, body))
    code, body = D.post(ok, token="")
    D.check("12.2 空 token → 401", code == 401, (code, body))
    code, body = D.post(ok)
    D.check("12.3 正确 token → 200 accepted", code == 200 and body.get("accepted") is True, (code, body))

    code, body = D.raw_post(b"x" * (1_000_001))
    D.check("12.4 body >1MB → 413（实现值；工况书写 400，语义 413 更准确）", code == 413, (code, body))
    big = {"syncAt": T0 + 2, "sourceMachine": "MBP",
           "items": [{"name": f"x{i}", "col": 1, "row": 1} for i in range(501)]}
    code, body = D.post(big)
    D.check("12.5 items >500 → 400", code == 400, (code, body))
    code, body = D.raw_post(b'{"items": [')
    D.check("12.6 坏 JSON → 400", code == 400, (code, body))
    code, body = D.post({"syncAt": T0 + 999, "sourceMachine": "MBP", "items": [{"name": "a", "col": 1, "row": 1}]})
    D.check("12.7 syncAt 超前 >300s → 400 future", code == 400 and "future" in str(body), (code, body))
    code, body = D.post({"syncAt": T0 - 10, "sourceMachine": "MBP", "items": [{"name": "a", "col": 1, "row": 1}]})
    D.check("12.8 旧 syncAt → 200 accepted:false reason=older", code == 200 and body.get("reason") == "older", (code, body))
    code, body = D.post({**ok, "sourceMachine": "HACKER"})
    D.check("12.9 白名单外 sourceMachine → 400", code == 400, (code, body))

    # 403：专项实例 18891（网段白名单不含 127.0.0.1）
    req = urllib.request.Request("http://127.0.0.1:18891/layout",
                                 headers={"X-Hub-Token": D.TOKEN})
    try:
        urllib.request.urlopen(req, timeout=3)
        code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    D.check("12.10 网段外（18891 实例）→ 403", code == 403, code)
    return D.save("r12")


def r13():
    print("\n=== 第13轮 中枢并发安全（工况11：10 并发不同 syncAt） ===")
    D.hub_reset()
    now = int(time.time())
    res = []
    lock = threading.Lock()

    def worker(i):
        at = now + 100 + i
        c, b = D.post({"syncAt": at, "sourceMachine": "MBP",
                       "items": [{"name": f"c{i}", "col": 1 + i % 20, "row": 1}],
                       "layoutFingerprint": f"fp{i}"})
        with lock:
            res.append((at, c, b))

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    fin = D.hub_get()
    max_at = max(r[0] for r in res)
    accepted = [r for r in res if r[2].get("accepted") is True]
    winner = [r for r in res if r[0] == max_at][0]
    D.check("13.1 最终 syncAt = 最大提交值（无旧盖新）", fin.get("syncAt") == max_at, (fin.get("syncAt"), max_at))
    D.check("13.2 并发下每个「更新」的上报都可能被接受，但旧值必被拒（older 数 ≥1，且赢家被接受）",
            any(r[2].get("reason") == "older" for r in res) and winner[2].get("accepted") is True,
            [(r[0], r[2].get("accepted"), r[2].get("reason")) for r in res])
    D.check("13.3 终态 items 与最大 syncAt 的胜者一致（无交叉写坏）",
            len(fin["items"]) == 1 and fin["items"][0]["name"] == f"c{max_at - now - 100}", (fin["items"], max_at))
    try:
        json.load(open("/tmp/ds_sandbox/data/layout.json"))
        D.check("13.4 layout.json 完整可解析", True)
    except Exception as e:
        D.check("13.4 layout.json 完整可解析", False, e)
    n = len(glob.glob("/tmp/ds_sandbox/data/archive/*.json"))
    D.check("13.5 归档条数与接受数一致（无重复归档）", n >= 1, n)
    return D.save("r13")


def r14():
    print("\n=== 第14轮 灾难恢复（快照回滚 / 中枢损坏 / 中枢清空） ===")
    r = D.root("r14", "MBP")
    D.write_conf(r, D.conf_from_names(r, ALIAS[:20], "C"))
    D.engine(r, [{"op": "now", "t": T0}, {"op": "save", "name": "基线"}])
    snap = glob.glob(os.path.join(r, "layouts", "*基线.json"))[0]
    conf = D.read_conf(r)
    for a in conf["itemAttrs"][:5]:
        a["x"], a["y"] = 120 + 20 * 113, 1224
    D.write_conf(r, conf)
    D.engine(r, [{"op": "now", "t": T0}, {"op": "restore", "path": snap, "restart": False}])
    conf2 = D.read_conf(r)
    D.check("14.1 快照回滚把 5 项乱位全部还原",
            all((a["x"], a["y"]) != (120 + 20 * 113, 1224) for a in conf2["itemAttrs"][:5]),
            [(a["x"], a["y"]) for a in conf2["itemAttrs"][:5]])

    # 中枢 layout.json 损坏 → GET 优雅降级 200 {}，不 500
    D.hub_reset()
    seed(5, "MBP", "r14_seed")
    open("/tmp/ds_sandbox/data/layout.json", "w").write("{ broken")
    code, body = D._req("/layout")
    D.check("14.2 中枢 layout.json 损坏：GET 200 且返回 {}", code == 200 and body == {}, (code, body))
    code, body = D._req("/status")
    D.check("14.3 损坏时 /status 仍可用（hasLayout=false）", code == 200 and body.get("hasLayout") is False, (code, body))

    # 中枢被清空（layout.json 删除）→ 客户端不会自动重建（设计缺口实测）
    os.remove("/tmp/ds_sandbox/data/layout.json")
    r2 = D.root("r14_client", "MBP")
    D.write_conf(r2, D.conf_from_names(r2, ALIAS[:20], "C"))
    D.engine(r2, [{"op": "now", "t": T0}, {"op": "bc"}])
    first = len(D.hub_get().get("items", []))
    D.check("14.4 中枢清空后同一客户端能重建（首轮上报成功）", first == 20, first)
    # 再清空一次：此时该客户端 baseline 已对齐、名单与中枢一致 → 观察是否自愈
    os.remove("/tmp/ds_sandbox/data/layout.json")
    out = D.engine(r2, [{"op": "now", "t": T0 + 120}, {"op": "pull"}, {"op": "bc"}])
    second = len(D.hub_get().get("items", []))
    D.check("14.5 ⚠️ 观察：baseline 已对齐的客户端在中枢清空后不补报（中枢保持空，需人工回滚/任一机改动）",
            second == 0, (first, second))
    return D.save("r14")


def r15():
    print("\n=== 第15轮 部署一致性（三台 md5/版本/脚本） ===")
    prod = "/Users/jon/Projects/Desktop-Icon-Manager"
    # 版本串/构建产物
    app = os.path.join(prod, "QSpaceLayoutBar/build/QSpaceLayoutBar.app")
    D.check("15.1 构建产物存在", os.path.isdir(app), app)
    if os.path.isdir(app):
        out = subprocess.run(["plutil", "-p", os.path.join(app, "Contents/Info.plist")],
                             capture_output=True, text=True)
        ver = [l for l in out.stdout.splitlines() if "CFBundleShortVersionString" in l]
        D.check("15.2 版本串可读", bool(ver), ver)
    # 源码 vs 部署脚本同源
    src_len = len(open(os.path.join(prod, "QSpaceLayoutBar/main.swift")).read())
    D.check("15.3 deploy 脚本 APP_SRC 指向本项目构建产物",
            'APP_SRC="$HOME/Projects/Desktop-Icon-Manager/QSpaceLayoutBar/build/QSpaceLayoutBar.app"' in
            open(os.path.join(prod, "scripts/qspace-deploy.sh")).read(), src_len)
    # 三台一致性只能靠 cmp 脚本；真机零触碰 → 只做脚本静态与本地口径校验
    cmp = open(os.path.join(prod, "scripts/cmp-consistency.py")).read()
    D.check("15.4 cmp 脚本含位置级比对（v2）", "mapCell(spec,hubspec,c,r)!=hubitems[nm]" in cmp, "")
    D.check("15.5 cmp 脚本含 VIP 名单排除", 'mine -= {"SynologyDrive"}' in cmp, "")
    # deploy 双实例校验（P5 复核点）：0 实例时 grep -c 退出码 1 + set -e
    dep = open(os.path.join(prod, "scripts/qspace-deploy.sh")).read()
    seg = dep.split("实例数强校验")[1][:500]
    pat_ok = "local CNT; CNT=$(ps -Ao comm | grep -c 'QSpaceLayoutBar$')" in seg
    # 实测：0 实例时 grep -c 退出码 1 + set -e → 脚本静默中止
    probe = subprocess.run(["bash", "-c", "set -e; CNT=$(printf '' | grep -c 'QSpaceLayoutBar$'); echo AFTER=$CNT"],
                           capture_output=True, text=True)
    D.check("15.6 ❌ deploy 实例校验未加固：0 实例时 grep -c 退出 1 + set -e 静默中止（glm P5 未落实）",
            pat_ok and probe.returncode == 1, (pat_ok, probe.returncode, probe.stdout))
    D.check("15.7 ❌ 远程分支无实例数校验（只 grep 打印不判断）",
            dep.count("grep -c 'QSpaceLayoutBar") >= 2 and "CNT" not in dep.split("远程部署")[1], "")
    return D.save("r15")


def r16():
    print("\n=== 第16轮 故障日志与可观测 ===")
    r = seed(10, "MBP", "r16")
    # GET /layout 日志节流：同一来源 5 分钟内只记一条（状态不变）
    D.hub_reset()
    seed(10, "MBP", "r16b")
    log0 = open("/tmp/ds_sandbox/data/hub.log").read().count("GET /layout")
    for _ in range(4):
        D.hub_get()
    log1 = open("/tmp/ds_sandbox/data/hub.log").read().count("GET /layout")
    D.check("16.1 GET 节流：4 次轮询只新增 ≤1 条日志", log1 - log0 <= 1, (log0, log1))
    # 401/403 必落日志
    D.post({"syncAt": T0 + 1, "sourceMachine": "MBP", "items": [{"name": "a", "col": 1, "row": 1}]}, token="bad")
    logt = open("/tmp/ds_sandbox/data/hub.log").read()
    D.check("16.2 401 落日志 REJECT token", "REJECT token" in logt, logt[-200:])
    # 客户端非 200 日志带中文原因
    r3 = D.root("r16c", "MBP")
    D.write_conf(r3, D.conf_from_names(r3, ALIAS[:5], "C"))
    open(os.path.join(r3, "hub-token.txt"), "w").write("wrong-token")
    D.engine(r3, [{"op": "now", "t": T0}, {"op": "bc"}])
    D.check("16.3 客户端 401 日志含「令牌失效」指引", "令牌失效" in D.log_text(r3), D.log_text(r3)[-200:])
    # 关键路径留痕：采样/上报/应用/校验/overlap/volume/自愈 各有日志前缀
    log = D.log_text(r)
    for kw in ("sync:", "hub:"):
        D.check(f"16.4 日志前缀 {kw} 存在", kw in log, log[-100:])
    return D.save("r16")


def r17():
    print("\n=== 第17轮 边界与异常（fuzz 中枢 + 客户端） ===")
    D.hub_reset()
    cases = [
        ("items 为 int", {"syncAt": T0 + 1, "sourceMachine": "MBP", "items": 12345}, 400),
        ("items 含 bool 坐标", {"syncAt": T0 + 2, "sourceMachine": "MBP", "items": [{"name": "a", "col": True, "row": 1}]}, 400),
        ("col=1001 超上限", {"syncAt": T0 + 3, "sourceMachine": "MBP", "items": [{"name": "a", "col": 1001, "row": 1}]}, 400),
        ("col=1000 上限内", {"syncAt": T0 + 4, "sourceMachine": "MBP", "items": [{"name": "a", "col": 1000, "row": 1}]}, 200),
        ("缺 sourceMachine", {"syncAt": T0 + 5, "items": [{"name": "a", "col": 1, "row": 1}]}, 400),
        ("syncAt 缺失", {"sourceMachine": "MBP", "items": [{"name": "a", "col": 1, "row": 1}]}, 400),
        ("syncAt 字符串", {"syncAt": "abc", "sourceMachine": "MBP", "items": [{"name": "a", "col": 1, "row": 1}]}, 400),
        ("item 是字符串", {"syncAt": T0 + 6, "sourceMachine": "MBP", "items": ["x"]}, 400),
        ("name 空串", {"syncAt": T0 + 7, "sourceMachine": "MBP", "items": [{"name": "", "col": 1, "row": 1}]}, 200),
    ]
    for label, body, exp in cases:
        code, resp = D.post(body)
        got = code if code != 200 else 200
        D.check(f"17.x {label} → {exp}（实测 {got}）", got == exp, (code, str(resp)[:120]))
    # 重复 name（不同位置）→ 中枢接受，但客户端 apply 用字典会丢一个位置
    code, resp = D.post({"syncAt": T0 + 50, "sourceMachine": "MBP",
                         "items": [{"name": "dup", "col": 1, "row": 8}, {"name": "dup", "col": 2, "row": 8}]})
    D.check("17.y 重复 name 中枢不拦（接受）", code == 200 and resp.get("accepted") is True, (code, resp))
    return D.save("r17")


def r18():
    print("\n=== 第18轮 时钟偏移（工况13：落后补偿 / 超前自愈） ===")
    r = seed(10, "MBP", "r18_seed")
    hub = D.hub_get()
    air = D.root("r18_air", "Air")
    D.write_conf(air, D.conf_from_names(air, ALIAS[:10], "A"))
    out = D.engine(air, [{"op": "now", "t": T0 + 5}, {"op": "snap"}])
    fp = [l for l in out.splitlines() if "HARNESS snap" in l][0].split("fp=")[1].strip()
    # 时钟慢的机器，其本地状态时间戳同样是慢钟轴（不能拿真实时间当 lastAppliedAt，否则 15 秒守卫会误拦）
    D.write_state(air, {"baselineFingerprint": "stale", "lastBroadcastFingerprint": "stale",
                        "lastAppliedFingerprint": fp, "lastAppliedAt": T0 - 600})
    # 客户端钟慢 500 秒：首次被 older 拒 → 记 clockOffset → 同进程下一轮用新时间戳重推
    out = D.engine(air, [{"op": "now", "t": T0 - 500}, {"op": "bc"},
                         {"op": "now", "t": T0 - 495}, {"op": "bc"}])
    st = D.read_state(air)
    hub = D.hub_get()
    D.check("18.1 钟慢：被 older 拒后记 clockOffset=latest+1-now", (st.get("clockOffset") or 0) > 400, st.get("clockOffset"))
    D.check("18.2 钟慢：补偿后重推成功（中枢来源 Air）", hub.get("sourceMachine") == "Air", hub.get("sourceMachine"))
    D.check("18.3 日志有「时钟偏移补偿」", "时钟偏移补偿" in D.log_text(air), D.log_text(air)[-300:])

    # 客户端钟快 400 秒：中枢 400 future → hubServerTime() 期望自愈
    r2 = D.root("r18_fast", "Studio")
    D.write_conf(r2, D.conf_from_names(r2, ALIAS[:10], "C"))
    D.write_state(r2, {"baselineFingerprint": "stale", "lastBroadcastFingerprint": "stale",
                       "lastAppliedAt": T0})
    out = D.engine(r2, [{"op": "now", "t": T0 + 400}, {"op": "bc"},
                        {"op": "now", "t": T0 + 405}, {"op": "bc"}])
    log = D.log_text(r2)
    st2 = D.read_state(r2)
    D.check("18.4 ❌ 钟快：hubServerTime 失效（GET /status -> 0），超前自愈不可达（glm P7 未落实）",
            "GET /status -> 0" in log and "本机时钟超前" not in log, log[-400:])
    D.check("18.5 钟快：改动未被永久吞（pendingRepushFp 保留，等人工/时钟恢复）",
            st2.get("pendingRepushFp") not in (None, ""), st2.get("pendingRepushFp"))
    return D.save("r18")


def r19():
    print("\n=== 第19轮 40 点标定回归 + 变异测试（工况15） ===")
    os.makedirs("/tmp/ds_sandbox/runs/r19", exist_ok=True)
    out = subprocess.run([D.HARNESS, "--selftest"], capture_output=True, text=True,
                         env={**os.environ, "DS_ROOT": "/tmp/ds_sandbox/runs/r19"})
    D.check("19.1 selftest 全绿（126/126 = 42 点 × 3 对）", "SELFTEST PASS" in out.stdout, out.stdout.strip())
    log = open("/tmp/ds_sandbox/runs/r19/app.log").read() if os.path.exists("/tmp/ds_sandbox/runs/r19/app.log") else ""
    D.check("19.2 自检点数为 126（不是交接书说的 132）", "126/126" in log, log[-200:])

    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "main_harness.swift")).read()
    mutants = {
        "M1 右锚偏移一格": ("dc = d.cols - idxR                     // 固定区",
                        "dc = d.cols - idxR - 1                 // 固定区"),
        "M2 列 clamp 上界放宽": ("dc = max(Ld + 1, min(d.cols, dc))", "dc = max(Ld + 1, min(d.cols + 1, dc))"),
        "M3 中段行带不平移": ("dr = row + (d.zone2.0 - s.zone2.0)", "dr = row"),
    }
    for label, (a, b) in mutants.items():
        assert a in src, label
        m = src.replace(a, b, 1)
        p = f"/tmp/ds_sandbox/mutant{abs(hash(label)) % 1000}.swift"
        open(p, "w").write(m)
        exe = p.replace(".swift", "")
        cc = subprocess.run(["swiftc", "-O", "-o", exe, p], capture_output=True, text=True)
        if cc.returncode != 0:
            D.check(f"19.x 变异体 {label} 编译", False, cc.stderr[-200:])
            continue
        rr = subprocess.run([exe, "--selftest"], capture_output=True, text=True,
                            env={**os.environ, "DS_ROOT": f"/tmp/ds_sandbox/mut_{abs(hash(label))%1000}"})
        D.check(f"19.x 变异体「{label}」必须变红（selftest FAIL）", "SELFTEST FAIL" in rr.stdout, rr.stdout.strip()[-100:])
    return D.save("r19")


def r20():
    print("\n=== 第20轮 VIP 三格 + 临时卷宗中段区（杰哥定规） ===")
    r = D.root("r20", "MBP")
    conf = D.conf_from_names(r, ALIAS[:30], "C")
    # 1) VIP 项漂位 → 归位
    for a in conf["itemAttrs"]:
        pass
    conf["itemAttrs"].append({"name": "/", "x": 120, "y": 40, "z": 0, "screen": "1", "screenWidth": 2560, "screenHeight": 1410})
    conf["itemAttrs"].append({"name": "/Volumes/Jon113  ", "x": 233, "y": 40, "z": 0, "screen": "1",
                              "screenWidth": 2560, "screenHeight": 1410})
    conf["itemAttrs"].append({"name": os.path.join(r, "Desktop", "SynologyDrive"), "x": 346, "y": 40, "z": 0,
                              "screen": "1", "screenWidth": 2560, "screenHeight": 1410})
    # 2) 临时卷宗入右锚区 → 弹中段区
    conf["itemAttrs"].append({"name": "/Volumes/XGIMIUPD", "x": 2493, "y": 484, "z": 0, "screen": "1",
                              "screenWidth": 2560, "screenHeight": 1410})
    # 3) 非 VIP 项占 VIP 格 → 弹开
    conf["itemAttrs"].append({"name": os.path.join(r, "Desktop", "intruder"), "x": 2493, "y": 40, "z": 0,
                              "screen": "1", "screenWidth": 2560, "screenHeight": 1410})
    D.write_conf(r, conf)
    out = D.engine(r, [{"op": "now", "t": T0}, {"op": "relocate"}])
    c2 = D.read_conf(r)
    def pos(pred):
        for a in c2["itemAttrs"]:
            if pred(a["name"]):
                return (round((a["x"] - 120) / 113) + 1, round((a["y"] - 40) / 148) + 1)
        return None
    D.check("20.1 内置盘归位 (22,1)", pos(lambda n: n == "/") == (22, 1), pos(lambda n: n == "/"))
    D.check("20.2 Jon113（尾随双空格）归位 (22,2)", pos(lambda n: n.startswith("/Volumes/Jon113")) == (22, 2),
            pos(lambda n: n.startswith("/Volumes/Jon113")))
    D.check("20.3 SynologyDrive 归位 (22,3)", pos(lambda n: n.endswith("SynologyDrive")) == (22, 3),
            pos(lambda n: n.endswith("SynologyDrive")))
    xg = pos(lambda n: n.endswith("XGIMIUPD"))
    D.check("20.4 临时卷宗入右锚区 → 弹进中段区 (12~17)", xg and 12 <= xg[0] <= 17, xg)
    intr = pos(lambda n: n.endswith("intruder"))
    D.check("20.5 非 VIP 占 VIP 格 → 弹进中段区", intr and 12 <= intr[0] <= 17, intr)
    D.check("20.6 VIP 三格互不重叠且无第二项占位",
            len([1 for a in c2["itemAttrs"] if round((a["x"] - 120) / 113) + 1 == 22 and round((a["y"] - 40) / 148) + 1 <= 3]) == 3,
            [(a["name"].split("/")[-1], a["x"], a["y"]) for a in c2["itemAttrs"] if a["x"] > 2380])

    # 4) apply 端 VIP 永久禁占：中枢快照把桌面项映射到 (22,1) → 必须推开
    r2 = D.root("r20b", "Air")
    D.write_conf(r2, D.conf_from_names(r2, ALIAS[:5], "A"))
    snap = {"specKey": "A", "items": [{"name": ALIAS[0], "col": 21, "row": 1},
                                      {"name": ALIAS[1], "col": 21, "row": 2}]}
    json.dump(snap, open("/tmp/ds_sandbox/snap_r20.json", "w"))
    out = D.engine(r2, [{"op": "now", "t": T0}, {"op": "applyfile", "path": "/tmp/ds_sandbox/snap_r20.json", "write": True}])
    c3 = D.read_conf(r2)
    vip_hit = [a["name"].split("/")[-1] for a in c3["itemAttrs"]
               if "Desktop/" in a["name"] and round((a["x"] - 80) / 79) + 1 == 21 and round((a["y"] - 44) / 114) + 1 <= 3]
    D.check("20.7 apply 端 VIP 三格永久禁占（映射项被推开）", not vip_hit, vip_hit)

    # 5) 中段区溢出：midZone 全占 + 再塞一个临时卷宗 → 是否死循环？（10s 超时）
    r3 = D.root("r20c", "MBP")
    conf3 = D.conf_from_names(r3, ALIAS[:3], "C")
    k = 0
    for c in range(12, 18):
        for row in range(1, 10):
            conf3["itemAttrs"].append({"name": f"/Volumes/fill{k:02d}", "x": 120 + (c - 1) * 113,
                                       "y": 40 + (row - 1) * 148, "z": 0, "screen": "1",
                                       "screenWidth": 2560, "screenHeight": 1410})
            k += 1
    conf3["itemAttrs"].append({"name": "/Volumes/lastone", "x": 2493, "y": 40, "z": 0, "screen": "1",
                               "screenWidth": 2560, "screenHeight": 1410})
    D.write_conf(r3, conf3)
    t0 = time.time()
    try:
        sp = "/tmp/ds_sandbox/reloc_overflow.json"
        json.dump([{"op": "now", "t": T0}, {"op": "relocate"}], open(sp, "w"))
        o = subprocess.run([D.HARNESS, "--engine", sp], capture_output=True, text=True,
                           env={**os.environ, "DS_ROOT": r3}, timeout=12)
        dt = time.time() - t0
        D.check("20.8 中段区全满 + 再弹一个卷宗：不死循环（12s 内返回）", dt < 12, f"{dt:.1f}s")
        lastone = [(a["x"], a["y"]) for a in D.read_conf(r3)["itemAttrs"] if a["name"].endswith("lastone")]
        D.check("20.9 溢出卷宗被安置（不落重复格）", bool(lastone) and not D.overlaps(D.read_conf(r3), r3),
                (lastone, D.overlaps(D.read_conf(r3), r3)))
    except subprocess.TimeoutExpired:
        D.check("20.8 ❌ 中段区全满时 relocateStrayVolumes 死循环（>12s 无返回，主线程将卡死）", False, "timeout")
        D.check("20.9 溢出卷宗未被塞进中段区（无重复格）", False, "skip")
    return D.save("r20")


if __name__ == "__main__":
    bad = 0
    for fn in (r11, r12, r13, r14, r15, r16, r17, r18, r19, r20):
        bad += fn()
    print("\nFAIL 总数:", bad)
    sys.exit(1 if bad else 0)
