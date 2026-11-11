#!/usr/bin/env python3
"""轮检台架 DSL：起沙箱根、跑 engine 脚本、打沙箱中枢、断言。

所有沙箱根在 /tmp/ds_sandbox/runs/<name>，中枢固定 127.0.0.1:18890（沙箱）。
"""
import json, os, shutil, subprocess, time, urllib.request, urllib.error
from collections import Counter

HARNESS = "/tmp/ds_sandbox/harness"
FIX = "/tmp/ds_sandbox/fixtures"
RUNS = "/tmp/ds_sandbox/runs"
HUB = "http://127.0.0.1:18890"
TOKEN = "sandbox-token-123"
EVID = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

RESULTS = []


# ---------------- 沙箱根 ----------------
def root(name, tag="MBP"):
    r = os.path.join(RUNS, name)
    shutil.rmtree(r, ignore_errors=True)
    for d in ("conf", "layouts", "Desktop"):
        os.makedirs(os.path.join(r, d), exist_ok=True)
    open(os.path.join(r, "machine-tag.txt"), "w").write(tag)
    open(os.path.join(r, "hub-token.txt"), "w").write(TOKEN)
    return r


def fixture(name, r, dest="conf/desktop.conf"):
    txt = open(os.path.join(FIX, name)).read().replace("{DS_ROOT}", r)
    open(os.path.join(r, dest), "w").write(txt)
    return json.loads(txt)


def conf_path(r):
    return os.path.join(r, "conf/desktop.conf")


def read_conf(r):
    return json.load(open(conf_path(r)))


def state_path(r):
    return os.path.join(r, "sync-state.json")


def read_state(r):
    try:
        return json.load(open(state_path(r)))
    except Exception:
        return {}


def write_state(r, st):
    json.dump(st, open(state_path(r), "w"), ensure_ascii=False, indent=1)


def log_text(r):
    try:
        return open(os.path.join(r, "app.log"), encoding="utf-8", errors="replace").read()
    except Exception:
        return ""


# ---------------- engine ----------------
def engine(r, steps, tag=None):
    if tag:
        open(os.path.join(r, "machine-tag.txt"), "w").write(tag)
    p = os.path.join(r, "engine.json")
    json.dump(steps, open(p, "w"), ensure_ascii=False)
    out = subprocess.run([HARNESS, "--engine", p], env={**os.environ, "DS_ROOT": r},
                         capture_output=True, text=True, timeout=120)
    return out.stdout + out.stderr


# ---------------- 中枢 ----------------
def _req(path, method="GET", body=None, token=TOKEN, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(HUB + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", "X-Hub-Token": token, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def hub_reset():
    _req("/reset", "POST", {})


def hub_status():
    return _req("/status")[1]


def hub_get():
    return _req("/layout")[1]


def post(body, token=TOKEN):
    return _req("/layout", "POST", body, token=token)


def raw_post(payload, token=TOKEN, extra_headers=None):
    """任意裸 payload（坏 JSON / 超大 body 用）。
    用 http.client 手工发：服务端对超大 body 会先回 413 再关连接 → urllib 会抛 BrokenPipe 拿不到状态码。"""
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", 18890, timeout=10)
    conn.putrequest("POST", "/layout")
    conn.putheader("Content-Type", "application/json")
    conn.putheader("X-Hub-Token", token)
    for k, v in (extra_headers or {}).items():
        conn.putheader(k, v)
    conn.putheader("Content-Length", str(len(payload)))
    conn.endheaders()
    try:
        conn.send(payload)
    except Exception:
        pass
    try:
        r = conn.getresponse()
        return r.status, r.read().decode()[:200]
    except Exception as e:
        return 0, repr(e)[:200]
    finally:
        conn.close()


# ---------------- 断言 / 结果 ----------------
def check(name, cond, detail=""):
    RESULTS.append({"name": name, "ok": bool(cond), "detail": str(detail)[:600]})
    print(("  ✓ " if cond else "  ✗ ") + name + ("" if cond else f"   [{str(detail)[:400]}]"))
    return bool(cond)


def save(tag):
    p = os.path.join(EVID, f"matrix-{tag}.json")
    json.dump(RESULTS, open(p, "w"), ensure_ascii=False, indent=1)
    bad = [x for x in RESULTS if not x["ok"]]
    print(f"\n== {tag}: {len(RESULTS)-len(bad)}/{len(RESULTS)} 通过；证据 {p} ==")
    n = len(bad)
    RESULTS.clear()
    return n


def items_of(conf, r, only_desktop=True):
    """conf itemAttrs → [(短名, col, row)]（按当前套参数换算）"""
    spec = spec_of(conf)
    out = []
    for a in conf.get("itemAttrs", []):
        n = str(a.get("name", ""))
        if only_desktop and not n.startswith(os.path.join(r, "Desktop") + "/"):
            continue
        out.append((n.split("/")[-1],
                    round((a["x"] - spec["x0"]) / spec["cw"]) + 1,
                    round((a["y"] - spec["y0"]) / spec["rh"]) + 1))
    return out


SPECS = {"A": dict(cols=21, rows=9, x0=80, y0=44, cw=79, rh=114),
         "B": dict(cols=19, rows=9, x0=53, y0=43, cw=90, rh=114),
         "C": dict(cols=22, rows=9, x0=120, y0=40, cw=113, rh=148)}


def spec_of(conf):
    a = conf.get("itemAttrs") or [{}]
    w = a[0].get("screenWidth") or 0
    if w >= 2400:
        return SPECS["C"]
    if abs(w - 1710) <= abs(w - 1728):
        return SPECS["A"] if abs(w - 1710) <= 40 else SPECS["B"]
    return SPECS["B"]


def cells(conf, r):
    return [(c, row) for _, c, row in items_of(conf, r)]


def overlaps(conf, r):
    cnt = Counter(cells(conf, r))
    return {k: v for k, v in cnt.items() if v > 1}


def snap_fp(r, steps=None):
    st = [{"op": "snap"}]
    out = engine(r, st)
    for line in out.splitlines():
        if "HARNESS snap" in line:
            return line
    return out


def now():
    return int(time.time())


# ---------------- 夹具工厂 ----------------
BLOCKED = {"A": [(10, 4), (11, 4), (12, 4)],
           "B": [(9, 4), (10, 4), (11, 4), (12, 4)],
           "C": [(1, 6), (2, 6), (3, 6), (1, 7), (2, 7), (3, 7), (4, 7)]}
TOPRIGHT = {"A": (9, 4), "B": (8, 4), "C": (6, 3)}
ARM = {"A": (4, 5, 7), "B": (4, 5, 7), "C": (3, 4, 6)}


def grid_cells(sk, n, start_col=1, skip_vip=True):
    """按套参数生成 n 个合法格（避开组件区/禁区/VIP 右三格）"""
    s = SPECS[sk]
    tr, trows = TOPRIGHT[sk]
    arm, z0, z1 = ARM[sk]
    cells = []
    r = 1
    while len(cells) < n and r <= s["rows"]:
        for c in range(1, s["cols"] + 1):
            if c <= tr and r <= trows:
                continue
            if c <= arm and z0 <= r <= z1:
                continue
            if (c, r) in BLOCKED[sk]:
                continue
            if skip_vip and c == s["cols"] and r <= 3:
                continue
            cells.append((c, r))
            if len(cells) >= n:
                break
        r += 1
    return cells


def conf_from_names(r, names, sk, screen_w=None, lod=None, tag_paths=True):
    """按名字列表铺进指定套网格（坐标严格套参数），返回 conf dict（未写盘）"""
    s = SPECS[sk]
    sw = screen_w if screen_w is not None else {"A": 1710, "B": 1728, "C": 2560}[sk]
    sh = {"A": 1112, "B": 1112, "C": 1410}[sk]
    cs = grid_cells(sk, len(names))
    attrs = []
    for nm, (c, row) in zip(names, cs):
        full = nm if nm.startswith("/") else (os.path.join(r, "Desktop") + "/" + nm)
        attrs.append({"name": full, "x": s["x0"] + (c - 1) * s["cw"], "y": s["y0"] + (row - 1) * s["rh"],
                      "z": 0, "screen": "1", "screenWidth": sw, "screenHeight": sh})
    conf = {"lod": lod if lod is not None else (96 if sk == "C" else 64),
            "itemHSpacing": 22 if sk == "B" else 11, "itemVSpacing": 0, "fontSize": 11,
            "snapToGrid": True, "labelPosition": 1, "useStacks": False, "previewIcons": True,
            "visible": True, "itemAttrs": attrs}
    return conf


def write_conf(r, conf):
    json.dump(conf, open(conf_path(r), "w"), ensure_ascii=False, indent=1)
    return conf


def desktop_names(r):
    """真夹具的桌面项短名（37 个，含 SynologyDrive）"""
    d = json.load(open(os.path.join(FIX, "conf_real.json")))
    return [a["name"].split("/")[-1] for a in d["itemAttrs"] if "Desktop/" in a["name"]]

