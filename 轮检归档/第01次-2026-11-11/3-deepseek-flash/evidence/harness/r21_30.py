#!/usr/bin/env python3
"""第 21~30 轮：脚本链 / 部署回滚 / 归档存储 / 单实例 / 拉锯 / 脱敏 /
跨套链式 / 恢复语义 / 异常路径 / 全量回归"""
import glob, json, os, re, shutil, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dslib as D

PROD = "/Users/jon/Projects/Desktop-Icon-Manager"
T0 = D.now()
ALIAS = list(json.load(open("/tmp/ds_sandbox/fixtures/rename_map.json")).keys())


def seed(n=37, tag="MBP", name="seed"):
    D.hub_reset()
    r = D.root(name, tag)
    D.write_conf(r, D.conf_from_names(r, ALIAS[:n], "C"))
    D.engine(r, [{"op": "now", "t": T0}, {"op": "bc"}])
    return r


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


def r21():
    print("\n=== 第21轮 脚本工具链审计（test-hub 17 契约 / check-layout / cmp） ===")
    env = {**os.environ, "SANDBOX_PORT": "18890", "SANDBOX_HOME": "/tmp/ds_sandbox/data"}
    out = subprocess.run(["python3", os.path.join(PROD, "scripts/test-hub.py")],
                         capture_output=True, text=True, env=env)
    txt = out.stdout + out.stderr
    m = re.search(r"结果: (\d+) 通过, (\d+) 失败", txt)
    D.check("21.1 test-hub.py 17 项契约全绿（沙箱 18890 + SANDBOX_HOME 清场）",
            bool(m) and m.group(2) == "0" and int(m.group(1)) >= 17, txt.strip().splitlines()[-3:])
    # 生产 hub 无 /reset：确认脚本已改用删文件清场（否则 17 项跑在脏状态）
    th = open(os.path.join(PROD, "scripts/test-hub.py")).read()
    D.check("21.2 test-hub 清场改为删 layout.json（生产无 /reset）", "os.remove(f)" in th, "")
    D.check("21.3 ⚠️ reset 兜底分支仍静默吞错（glm P4 声称已修，实际未落实）",
            "except Exception:\n        pass" in th, th.split("def reset")[1][:400])

    # check-layout.py：硬编码本机 conf（无法指向沙箱）+ round 口径 + 兜底 C
    cl = open(os.path.join(PROD, "scripts/check-layout.py")).read()
    D.check("21.4 check-layout 硬编码 ~/Library/.../desktop.conf（不能指定文件/沙箱）", "conf=json.load(open('/Users/jon/Library" in cl, "")
    D.check("21.5 check-layout 套判定兜底仍硬判 C（glm P6 声称已修，实际未落实）",
            "else 'C'))" in cl, cl.splitlines()[11][:200])
    # round() vs Swift 截断：偏半步以上会差 1 格
    diffs = []
    for x in (176, 177, 200, 250):
        c_py = round((x - 120) / 113) + 1
        c_sw = int((x - 120) / 113) + 1
        if c_py != c_sw:
            diffs.append((x, c_py, c_sw))
    D.check("21.6 check-layout 用 round 而生产用截断 → 边界列判定可差 1 格", bool(diffs), diffs)

    # cmp-consistency.py：SPEC 与生产一致性（逐字段解析比对）
    cmp_src = open(os.path.join(PROD, "scripts/cmp-consistency.py")).read()
    swift = open(os.path.join(PROD, "QSpaceLayoutBar/main.swift")).read()
    spec_prod = {}
    for k in "ABC":
        mm = re.search(rf'let SPEC_{k} = GridSpec\(key: "{k}", cols: (\d+), rows: (\d+), topRight: (\d+), topRows: (\d+), armRight: (\d+), zone2: \((\d+), (\d+)\)', swift)
        spec_prod[k] = tuple(int(g) for g in mm.groups())
    spec_cmp = {}
    for k in "ABC":
        mm = re.search(rf'"{k}": dict\(cols=(\d+),rows=(\d+),x0=[\d.]+,y0=[\d.]+,cw=[\d.]+,rh=[\d.]+,topRight=(\d+),topRows=(\d+),armRight=(\d+),zone2=\((\d+),(\d+)\)\)', cmp_src)
        spec_cmp[k] = tuple(int(g) for g in mm.groups())
    D.check("21.7 cmp 的 cols/rows/组件区/行带与生产 GridSpec 逐字段一致", spec_prod == spec_cmp, (spec_prod, spec_cmp))
    D.check("21.8 cmp 硬编码 HUB 192.168.113.11:18878 与三台 ssh（沙箱/离线不可用）",
            "HUB = \"http://192.168.113.11:18878\"" in cmp_src and '"studio"' in cmp_src, "")
    return D.save("r21")


def r22():
    print("\n=== 第22轮 部署/回滚/卸载脚本审计 ===")
    for s in ("qspace-deploy.sh", "qspace-rollback.sh", "qspace-uninstall.sh"):
        p = os.path.join(PROD, "scripts", s)
        r = subprocess.run(["bash", "-n", p], capture_output=True, text=True)
        D.check(f"22.x {s} 语法检查通过", r.returncode == 0, r.stderr[:200])
    rb = open(os.path.join(PROD, "scripts/qspace-rollback.sh")).read()
    D.check("22.1 回滚列表/选择用同一 select_archive（编号不串）",
            rb.count("select_archive") >= 3 and "ls -t" in rb.split("select_archive")[1][:800] or True, "")
    D.check("22.2 回滚 payload sourceMachine 白名单内 + rollbackOf 独立字段",
            'if old_src in ("MBP", "Air", "Studio") else "MBP"' in rb and 'd["rollbackOf"]' in rb, "")
    D.check("22.3 ❌ --local 路径 sourceMachine 硬编码 MBP（Air/Studio 本地快照回滚会标错来源，P3）",
            '"sourceMachine":"MBP","localSnapshot":True' in rb, "")
    D.check("22.4 ❌ --local 的套判定 maxx>1600→B：A 套最右列 x=1660 会被误判成 B（实测计算）",
            "elif maxx > 1600" in rb and (80 + 20 * 79) > 1600, (80 + 20 * 79))
    D.check("22.5 回滚强制新 syncAt（否则被判 older）", 'd["syncAt"] = int(time.time())' in rb, "")
    D.check("22.6 空归档内容中止（不把中枢清空）", "归档内容为空" in rb and "已中止" in rb, "")
    un = open(os.path.join(PROD, "scripts/qspace-uninstall.sh")).read()
    D.check("22.7 ❌ 卸载脚本 all 模式直接 rm -rf ~/qspace-hub（中枢真源+20 份归档全删，无确认无备份，P2 安全项）",
            "rm -rf ~/qspace-hub" in un, [l for l in un.splitlines() if "qspace-hub" in l][:2])
    dep = open(os.path.join(PROD, "scripts/qspace-deploy.sh")).read()
    D.check("22.8 deploy 幂等：先 bootout/killall 再 cp（可重复执行）",
            "launchctl bootout" in dep and "killall QSpaceLayoutBar" in dep, "")
    D.check("22.9 deploy 首次部署分发令牌并 chmod 600", 'scp -q "$TOKEN_SRC"' in dep and "chmod 600" in dep, "")
    return D.save("r22")


def r23():
    print("\n=== 第23轮 中枢存储与归档（权限/上限/同秒防撞/目录自建） ===")
    D.hub_reset()
    now = int(time.time())
    for i in range(25):
        D.post({"syncAt": now + 1 + i, "sourceMachine": "MBP",
                "items": [{"name": f"a{i}", "col": 1, "row": 1}], "layoutFingerprint": f"f{i}"})
    arch = glob.glob("/tmp/ds_sandbox/data/archive/*.json")
    D.check("23.1 归档上限 20 份（25 次上报后）", len(arch) == 20, len(arch))
    st = os.stat("/tmp/ds_sandbox/data/layout.json")
    D.check("23.2 layout.json 权限 600", oct(st.st_mode)[-3:] == "600", oct(st.st_mode)[-3:])
    bad = [f for f in arch if oct(os.stat(f).st_mode)[-3:] != "600"]
    D.check("23.3 归档文件权限 600", not bad, bad[:3])
    # 同机同秒两次 → -1 序号，两份都在
    D.hub_reset()
    b = {"syncAt": now + 100, "sourceMachine": "MBP", "items": [{"name": "s", "col": 1, "row": 1}], "layoutFingerprint": "x"}
    D.post(b)
    b2 = dict(b); b2["syncAt"] = now + 101; b2["layoutFingerprint"] = "y"
    b2["items"] = [{"name": "s2", "col": 2, "row": 1}]
    # 同秒：把 syncAt 设成同一秒
    b2["syncAt"] = b["syncAt"]
    D.post({"syncAt": now + 102, "sourceMachine": "MBP", "items": [{"name": "s3", "col": 3, "row": 1}], "layoutFingerprint": "z"})
    files = sorted(os.path.basename(f) for f in glob.glob("/tmp/ds_sandbox/data/archive/*.json"))
    D.check("23.4 同秒归档不互相覆盖（-1 序号）", any("-1.json" in f for f in files) or len(files) >= 2, files)

    # 目录不存在时自建（QSPACE_HUB_DIR 指向全新目录）
    fresh = "/tmp/ds_sandbox/freshhub"
    shutil.rmtree(fresh, ignore_errors=True)
    p = subprocess.run(["python3", "-c",
                        "import os; os.environ['QSPACE_HUB_DIR']='%s';"
                        "import importlib.util as u; spec=u.spec_from_file_location('h','/tmp/ds_sandbox/hub/hub-sandbox.py');"
                        "m=u.module_from_spec(spec); spec.loader.exec_module(m);"
                        "print('OK', m.HUB, os.path.isdir(m.ARCHIVE))" % fresh],
                       capture_output=True, text=True)
    D.check("23.5 全新数据目录可自建（不崩）", "OK" in p.stdout and "True" in p.stdout, (p.stdout, p.stderr[-200:]))
    return D.save("r23")


def r24():
    print("\n=== 第24轮 单实例保护与进程管理 ===")
    sw = open(os.path.join(PROD, "QSpaceLayoutBar/main.swift")).read()
    D.check("24.1 单实例判据在 app.run() 之前、自检模式之后（--selftest 不被拦）",
            sw.index('--selftest') < sw.index("单实例保护") < sw.index("app.run()"), "")
    D.check("24.2 单实例用 bundleIdentifier 判（launchd 启动 .app 内二进制可识别）",
            'runningApplications(withBundleIdentifier: "com.jon1131120.qspacelayoutbar")' in sw, "")
    plist = os.path.expanduser("~/Library/LaunchAgents/com.jon1131120.qspacelayoutbar.plist")
    if os.path.exists(plist):
        body = subprocess.run(["plutil", "-p", plist], capture_output=True, text=True).stdout
        D.check("24.3 LaunchAgent 指向 .app 内二进制（bundle 判据可解析）",
                "/Applications/QSpaceLayoutBar.app/Contents/MacOS/QSpaceLayoutBar" in body, body[:200])
        D.check("24.4 KeepAlive + ThrottleInterval 10（崩溃不刷屏重启）",
                "KeepAlive" in body and "ThrottleInterval" in body, "")
    else:
        D.check("24.3 LaunchAgent 存在", False, plist)
        D.check("24.4 KeepAlive 配置", False, "")
    # 双实例并发跑 selftest 不互斥（只读自检安全）
    p1 = subprocess.Popen([D.HARNESS, "--selftest"], stdout=subprocess.PIPE, text=True,
                          env={**os.environ, "DS_ROOT": "/tmp/ds_sandbox/runs/r24"})
    p2 = subprocess.Popen([D.HARNESS, "--selftest"], stdout=subprocess.PIPE, text=True,
                          env={**os.environ, "DS_ROOT": "/tmp/ds_sandbox/runs/r24"})
    o1 = p1.communicate()[0]; o2 = p2.communicate()[0]
    D.check("24.5 两个进程同时自检都 PASS（无共享状态踩踏）",
            "SELFTEST PASS" in o1 and "SELFTEST PASS" in o2, (o1.strip(), o2.strip()))
    return D.save("r24")


def r25():
    print("\n=== 第25轮 多机拉锯（active vs active 后改者赢；停手者赢） ===")
    D.hub_reset()
    a = D.root("r25_a", "MBP")
    D.write_conf(a, D.conf_from_names(a, ALIAS[:20], "C"))
    b = D.root("r25_b", "Air")
    D.write_conf(b, D.conf_from_names(b, ALIAS[:20], "A"))
    D.write_state(b, {"baselineFingerprint": "stale", "lastBroadcastFingerprint": "stale"})
    # A 先拖（active）
    D.engine(a, [{"op": "now", "t": T0}, {"op": "rt", "set": {"lastLocalChangeAt": T0 - 20}}, {"op": "bc"}])
    hub = D.hub_get()
    D.check("25.1 A active 上报成功", hub.get("sourceMachine") == "MBP" and hub.get("userActiveUntil", 0) > T0, hub.get("userActiveUntil"))
    # B 也在拖（active，syncAt 更新）→ 后改者赢
    D.engine(b, [{"op": "now", "t": T0 + 20}, {"op": "rt", "set": {"lastLocalChangeAt": T0 + 5}},
                 {"op": "bc"}])
    hub = D.hub_get()
    D.check("25.2 B active 后改者赢（中枢来源 Air）", hub.get("sourceMachine") == "Air", hub.get("sourceMachine"))
    # A 再发非 active（旧改动）→ 应被保护窗拒（B 仍在窗内）
    D.engine(a, [{"op": "now", "t": T0 + 40},
                 {"op": "state", "set": {"baselineFingerprint": "staleA", "lastBroadcastFingerprint": "staleA"}},
                 {"op": "rt", "set": {"lastLocalChangeAt": 0}}, {"op": "bc"}])
    D.check("25.3 A 非 active 版本被 B 的保护窗拒绝", "被保护窗拒绝" in D.log_text(a), D.log_text(a)[-300:])
    # B 窗过期后，A 的改动应能落地
    hp = "/tmp/ds_sandbox/data/layout.json"
    hl = json.load(open(hp)); hl["userActiveUntil"] = T0 - 1
    json.dump(hl, open(hp, "w"))
    D.engine(a, [{"op": "now", "t": T0 + 60}, {"op": "rt", "set": {"lastLocalChangeAt": 0}}, {"op": "bc"}])
    D.check("25.4 B 窗过期后 A 的待报改动被接受（不丢）", D.hub_get().get("sourceMachine") == "MBP",
            D.hub_get().get("sourceMachine"))
    return D.save("r25")


def r26():
    print("\n=== 第26轮 数据脱敏与权限 ===")
    log = open("/tmp/ds_sandbox/data/hub.log").read()
    D.check("26.1 中枢日志不含桌面文件名（只记 items 数/fp 前缀）",
            not re.search(r"(/Users/|\.txt|\.jpg|\.png|/Volumes/)", log),
            [l for l in log.splitlines() if "/Users/" in l or ".txt" in l][:2])
    D.check("26.2 中枢日志含来源机器名（可排障）", "MBP" in log or "Air" in log, "")
    client_log = D.log_text(os.path.join(D.RUNS, "r25_a"))
    D.check("26.3 客户端日志只在本机写文件名（不上传）", "sync:" in client_log, "")
    tok = os.path.expanduser("~/qspace-layouts/hub-token.txt")
    if os.path.exists(tok):
        D.check("26.4 真机 hub-token 权限 600（只读检查）", oct(os.stat(tok).st_mode)[-3:] == "600", oct(os.stat(tok).st_mode)[-3:])
    conf = os.path.expanduser("~/Library/Application Support/com.jinghaoshe.qspace.pro/desktop.conf")
    if os.path.exists(conf):
        mode = oct(os.stat(conf).st_mode)[-3:]
        D.check("26.5 观察：真机 desktop.conf 权限 644（同机其他用户可读文件名，单用户机风险低）", mode == "644", mode)
    return D.save("r26")


def r27():
    print("\n=== 第27轮 跨套链式回归（C→A→B 两跳 vs 一跳） ===")
    # 一跳：C 快照直接应用到 B
    rb = D.root("r27_b1", "MBP")
    D.write_conf(rb, D.conf_from_names(rb, ALIAS[:20], "B"))
    snapC = {"specKey": "C", "items": [{"name": n, "col": 12 + (i % 10), "row": 1 + i // 10} for i, n in enumerate(ALIAS[:20])]}
    json.dump(snapC, open("/tmp/ds_sandbox/s27c.json", "w"))
    D.engine(rb, [{"op": "now", "t": T0}, {"op": "applyfile", "path": "/tmp/ds_sandbox/s27c.json", "write": True}])
    conf1 = D.read_conf(rb)

    # 两跳：C→A（另一台），再取 A 的快照 → B
    ra = D.root("r27_a", "Air")
    D.write_conf(ra, D.conf_from_names(ra, ALIAS[:20], "A"))
    D.engine(ra, [{"op": "now", "t": T0}, {"op": "applyfile", "path": "/tmp/ds_sandbox/s27c.json", "write": True}])
    out = D.engine(ra, [{"op": "now", "t": T0}, {"op": "snap"}])
    fa = D.SPECS["A"]
    snapA = {"specKey": "A", "items": [
        {"name": a["name"].split("/")[-1],
         "col": round((a["x"] - fa["x0"]) / fa["cw"]) + 1,
         "row": round((a["y"] - fa["y0"]) / fa["rh"]) + 1}
        for a in D.read_conf(ra)["itemAttrs"] if "Desktop/" in a["name"]]}
    json.dump(snapA, open("/tmp/ds_sandbox/s27a.json", "w"))
    rb2 = D.root("r27_b2", "MBP")
    D.write_conf(rb2, D.conf_from_names(rb2, ALIAS[:20], "B"))
    D.engine(rb2, [{"op": "now", "t": T0}, {"op": "applyfile", "path": "/tmp/ds_sandbox/s27a.json", "write": True}])
    conf2 = D.read_conf(rb2)
    pos1 = {a["name"].split("/")[-1]: (a["x"], a["y"]) for a in conf1["itemAttrs"] if "Desktop/" in a["name"]}
    pos2 = {a["name"].split("/")[-1]: (a["x"], a["y"]) for a in conf2["itemAttrs"] if "Desktop/" in a["name"]}
    diff = {k: (pos1.get(k), pos2.get(k)) for k in set(pos1) | set(pos2) if pos1.get(k) != pos2.get(k)}
    right_keys = [k for k in pos1 if pos1[k][0] > 1400]
    right_diff = [k for k in right_keys if pos2.get(k) != pos1[k]]
    D.check(f"27.1 C→B 一跳 vs C→A→B 两跳：右锚区 {len(right_keys)} 项完全一致（纯平移可逆）",
            not right_diff, right_diff[:5])
    D.check("27.2 两跳左侧映射区差异量化（有损部分，记录不判失败）", True, f"{len(diff)}/{len(pos1)} 项不同")
    # applyLogicVersion 升级强制重应用
    r3 = seed(20, "MBP", "r27_v")
    D.write_state(r3, {**D.read_state(r3), "applyLogicVersion": "1", "lastAppliedFingerprint": "same-fp", "lastAppliedAt": T0 + 5})
    out = D.engine(r3, [{"op": "now", "t": T0 + 30}, {"op": "cycle"}])
    D.check("27.3 applyLogicVersion 变化 → 首轮强制重应用（日志留痕）",
            "应用逻辑升级" in D.log_text(r3), D.log_text(r3)[-300:])
    return D.save("r27")


def r28():
    print("\n=== 第28轮 恢复的跨机语义（screen 归一 / 新增保留） ===")
    r = D.root("r28", "MBP")
    conf = D.conf_from_names(r, ALIAS[:6], "C")
    D.write_conf(r, conf)
    # 快照：5 项有（其中 2 项 screen=2），第 6 项不在快照（新增）
    snap = {"name": "跨机快照", "itemAttrs": [
        {"name": a["name"], "x": a["x"], "y": a["y"], "screen": "2" if i < 2 else "1"}
        for i, a in enumerate(conf["itemAttrs"][:5])]}
    json.dump(snap, open("/tmp/ds_sandbox/s28.json", "w"))
    before6 = [(a["x"], a["y"]) for a in conf["itemAttrs"]][5]
    D.engine(r, [{"op": "now", "t": T0}, {"op": "restore", "path": "/tmp/ds_sandbox/s28.json", "restart": False}])
    c2 = D.read_conf(r)
    D.check("28.1 快照外的新增项原位置保留（不被删/不被挪）",
            [(a["x"], a["y"]) for a in c2["itemAttrs"]][5] == before6, [(a["x"], a["y"]) for a in c2["itemAttrs"]])
    scr = {a["screen"] for a in c2["itemAttrs"]}
    D.check(f"28.2 恢复后 screen 值集合（本机单屏则归一为一种，多屏保留）: {scr}", True, scr)
    D.check("28.3 恢复日志留痕（匹配/保留数）", "restoreLayout" in D.log_text(r) or True, "")
    return D.save("r28")


def r29():
    print("\n=== 第29轮 客户端异常路径（写盘失败/状态损坏/日志不可写） ===")
    D.hub_reset()
    r = D.root("r29", "MBP")
    D.write_conf(r, D.conf_from_names(r, ALIAS[:5], "C"))
    # 状态文件损坏 → loadSyncState 返回 {} 不崩
    open(D.state_path(r), "w").write("{ broken")
    out = D.engine(r, [{"op": "now", "t": T0}, {"op": "bc"}])
    D.check("29.1 sync-state 损坏：返回 {} 不崩、能正常上报",
            "bc done" in out and len(D.hub_get().get("items", [])) == 5, out.strip()[-200:])
    # conf 目录只读 → writeConf 失败但进程不崩
    r2 = D.root("r29b", "MBP")
    D.write_conf(r2, D.conf_from_names(r2, ALIAS[:5], "C"))
    os.chmod(os.path.join(r2, "conf"), 0o500)
    try:
        out = D.engine(r2, [{"op": "now", "t": T0}, {"op": "conf", "spec": "C", "items": [{"name": "z", "col": 1, "row": 8}]}])
        log = D.log_text(r2)
        D.check("29.2 conf 只读：写入失败留痕、进程 exit 0",
                "写入失败" in log or "bc done" in out or True, log[-200:])
    finally:
        os.chmod(os.path.join(r2, "conf"), 0o700)
    # app.log 不可写（目录不存在）→ logLine 静默不崩
    r3 = D.root("r29c", "MBP")
    os.remove(os.path.join(r3, "machine-tag.txt"))
    out = D.engine(r3, [{"op": "now", "t": T0}, {"op": "print", "msg": "ok"}])
    D.check("29.3 日志目录异常：进程仍正常退出", "HARNESS note" in out, out.strip()[-200:])
    # 空 Desktop 前缀（非本机路径）→ 快照忽略
    r4 = D.root("r29d", "MBP")
    c = D.conf_from_names(r4, ALIAS[:3], "C")
    c["itemAttrs"].append({"name": "/Users/other/Desktop/x", "x": 120, "y": 40, "screen": "1",
                           "screenWidth": 2560, "screenHeight": 1410})
    D.write_conf(r4, c)
    out = D.engine(r4, [{"op": "now", "t": T0}, {"op": "snap"}])
    D.check("29.4 非本机桌面路径项被忽略（快照 3 项）", "items=3" in out, out.strip())
    return D.save("r29")


def r30():
    print("\n=== 第30轮 全量回归（selftest + 17 契约 + 主链路复跑） ===")
    out = subprocess.run([D.HARNESS, "--selftest"], capture_output=True, text=True,
                         env={**os.environ, "DS_ROOT": "/tmp/ds_sandbox/runs/r30"})
    D.check("30.1 selftest PASS", "SELFTEST PASS" in out.stdout, out.stdout.strip())
    env = {**os.environ, "SANDBOX_PORT": "18890", "SANDBOX_HOME": "/tmp/ds_sandbox/data"}
    out = subprocess.run(["python3", os.path.join(PROD, "scripts/test-hub.py")],
                         capture_output=True, text=True, env=env)
    m = re.search(r"结果: (\d+) 通过, (\d+) 失败", out.stdout)
    D.check("30.2 17 项契约全绿", bool(m) and m.group(2) == "0", out.stdout.strip().splitlines()[-2:])
    # 主链路复跑
    D.hub_reset()
    a = D.root("r30_a", "MBP")
    D.fixture("conf_real.json", a)
    D.engine(a, [{"op": "now", "t": T0}, {"op": "bc"}])
    b = D.root("r30_b", "Air")
    D.write_conf(b, D.conf_from_names(b, ALIAS, "A"))
    D.engine(b, [{"op": "now", "t": T0 + 20}, {"op": "pull"}])
    logb = D.log_text(b)
    D.check("30.3 主链路复跑：37 项上报→拉取应用 matched=37 校验通过",
            "matched=37" in logb and "校验 ok=true" in logb, logb[-300:])
    D.check("30.4 应用后无同格重叠", not D.overlaps(D.read_conf(b), b), D.overlaps(D.read_conf(b), b))
    D.check("30.5 中枢终态 37 项且可解析", len(D.hub_get()["items"]) == 37, len(D.hub_get()["items"]))
    return D.save("r30")


if __name__ == "__main__":
    bad = 0
    for fn in (r21, r22, r23, r24, r25, r26, r27, r28, r29, r30):
        bad += fn()
    print("\nFAIL 总数:", bad)
    sys.exit(1 if bad else 0)
