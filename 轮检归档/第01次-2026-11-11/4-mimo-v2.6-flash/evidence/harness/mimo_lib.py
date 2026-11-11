#!/usr/bin/env python3
"""客户端台架驱动（DS_ROOT 沙箱，生产 main.swift 生成的 harness 二进制）。"""
import json, os, re, shutil, subprocess, sys, time, urllib.request

HARNESS = "/tmp/mimo_sandbox/harness"
ROOT = "/tmp/mimo_sandbox/root"
BASE = "http://127.0.0.1:18890"
TOKEN = "mimo-sandbox-token-1"
EV = "/Users/jon/Projects/Desktop-Icon-Manager/轮检归档/第01次-2026-11-11/4-mimo-v2.6-flash/evidence"
NOW = int(time.time())

def reset_root(tag="MBP", n=37, spec="B", prefix="f"):
    """清空沙箱根目录并造 n 项桌面 conf（spec 套）。"""
    shutil.rmtree(ROOT, ignore_errors=True)
    os.makedirs(ROOT + "/conf", exist_ok=True)
    os.makedirs(ROOT + "/Desktop", exist_ok=True)
    os.makedirs(ROOT + "/layouts", exist_ok=True)
    open(ROOT + "/machine-tag.txt", "w").write(tag)
    open(ROOT + "/hub-token.txt", "w").write(TOKEN)
    write_conf(n, spec, prefix)

def write_conf(n, spec="B", prefix="f", start=1, extra=None, lod=True, shift=0):
    """经引擎 op=conf 写 conf（用生产 writeConf 路径）。
    格位避开 VIP 三格 (cols,1..3) 与右锚区，且两两不重（上报自检会拒同格）。"""
    SZ = {"A": (1710, 1117), "B": (1728, 1117), "C": (2560, 1440)}[spec]
    items = [{"name": f"/tmp/mimo_sandbox/root/Desktop/{prefix}{i:02d}.txt",
              "col": ((i + shift) % 14) + 1, "row": (i % 9) + 1,
              "screenWidth": SZ[0], "screenHeight": SZ[1]} for i in range(start, start + n)]
    if extra: items.extend(extra)
    script = [{"op": "now", "t": int(time.time())},
              {"op": "conf", "spec": spec, "items": items}]
    if not lod: script[1]["nolod"] = True
    return run(script)

def run(script, timeout=30, root=None):
    """跑引擎脚本 JSON，返回 (exit, stdout, stderr)。"""
    p = "/tmp/mimo_sandbox/eng.json"
    json.dump(script, open(p, "w"), ensure_ascii=False)
    env = dict(os.environ, DS_ROOT=root or ROOT)
    r = subprocess.run([HARNESS, "--engine", p], capture_output=True, text=True,
                       timeout=timeout, env=env)
    return r.returncode, r.stdout, r.stderr

def hub_reset():
    try: os.remove("/tmp/mimo_sandbox/data/layout.json")
    except OSError: pass
    d = "/tmp/mimo_sandbox/data/archive"
    if os.path.isdir(d):
        for f in os.listdir(d):
            if f.endswith(".json"):
                try: os.remove(os.path.join(d, f))
                except OSError: pass

def hub_get():
    req = urllib.request.Request(BASE + "/layout", headers={"X-Hub-Token": TOKEN})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=5).read())
    except Exception as e:
        return {"err": str(e)}

def hub_status():
    req = urllib.request.Request(BASE + "/status", headers={"X-Hub-Token": TOKEN})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())

def hub_posts():
    log = open("/tmp/mimo_sandbox/data/hub.log", encoding="utf-8", errors="ignore").read()
    return len(re.findall(r"-> ACCEPTED", log))

def conf_load():
    try: return json.load(open(ROOT + "/conf/desktop.conf"))
    except Exception as e: return {"err": str(e)}

def state_load():
    try: return json.load(open(ROOT + "/sync-state.json"))
    except Exception: return {}

def applog():
    try: return open(ROOT + "/app.log", encoding="utf-8", errors="ignore").read()
    except Exception: return ""

def snap_names():
    return sorted(i["name"] for i in hub_get().get("items", []))

results = []
def rec(name, ok, detail=""):
    results.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})
    print(("  ✅ " if ok else "  ❌ ") + name + " | " + str(detail)[:220])

def dump(fname, extra=None):
    out = {"results": results}
    if extra: out.update(extra)
    json.dump(out, open(f"{EV}/{fname}", "w"), ensure_ascii=False, indent=1)
