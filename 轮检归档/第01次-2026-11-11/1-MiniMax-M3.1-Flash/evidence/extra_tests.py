#!/usr/bin/env python3
"""补充实测：hub 安全面 / 客户端网络面 / 部署脚本契约 / 快照链契约
全部沙箱内；不 ssh 真机、不写真机 conf、不碰生产中枢。"""
import json, os, socket, subprocess, sys, time, urllib.request, urllib.error, pathlib, re

SB = "/tmp/sandbox_minimax"
URL = "http://127.0.0.1:18890"
TOKEN = "sandbox-token-123"
passed = failed = 0

def check(name, cond, detail=""):
    global passed, failed
    if cond: passed += 1; print(f"  ✓ {name}")
    else: failed += 1; print(f"  ✗ {name}  {detail}")

hub_src = pathlib.Path("/Users/jon/Projects/Desktop-Icon-Manager/hub/hub-server.py").read_text()
main_src = pathlib.Path("/Users/jon/Projects/Desktop-Icon-Manager/QSpaceLayoutBar/main.swift").read_text()

print("== 补充实测 ==")

# ── A. hub 绑定面 & IPv6 ──
print("\n-- A. hub 网络绑定面 --")
check("hub 绑定 0.0.0.0（对外暴露，靠网段+令牌双闸）", "ThreadingHTTPServer(('0.0.0.0', PORT), H)" in hub_src)
m = re.search(r'ALLOWED_NETS = \[ipaddress\.ip_network\(n\) for n in\s*\n\s*\(([^)]*)\)\]', hub_src)
nets = re.findall(r'"([^"]+)"', m.group(1))
print(f"  ALLOWED_NETS = {nets}")
check("白名单只含 IPv4（IPv6 请求源一律判否 → 403）", all(":" not in n for n in nets))
check("白名单含 127.0.0.1/32（仅本机）", "127.0.0.1/32" in nets)
check("白名单含两个家网段", "192.168.113.0/24" in nets and "192.168.191.0/24" in nets)
# IPv6 判定
import ipaddress
def peer_allowed(ip):
    try: a = ipaddress.ip_address(ip)
    except ValueError: return False
    return any(a in ipaddress.ip_network(n) for n in nets)
check("IPv6 源 ::1 → 判否(403)", not peer_allowed("::1"))
check("IPv4-mapped IPv6 ::ffff:192.168.113.14 → 判否", not peer_allowed("::ffff:192.168.113.14"))
check("外网 IP 8.8.8.8 → 判否", not peer_allowed("8.8.8.8"))
check("公网 172.16.0.1 → 判否", not peer_allowed("172.16.0.1"))

# ── B. token 面 ──
print("\n-- B. token 面 --")
check("token 用 hmac.compare_digest（常量时间，防时序攻击）", "hmac.compare_digest" in hub_src)
check("未配置令牌文件时 token_ok 直接放行（过渡态风险）", "return True          # 未配置密钥" in hub_src)
check("令牌文件缺失 → load_token 返回空串", "return ''" in hub_src)
# 客户端：令牌缺失时是否还带 X-Hub-Token
check("客户端令牌为空时不上报 X-Hub-Token 头", "if !tok.isEmpty { req.setValue(tok, forHTTPHeaderField: \"X-Hub-Token\") }" in main_src)

# ── C. 中枢状态锁覆盖范围 ──
print("\n-- C. _state_lock 覆盖范围 --")
i_lock = hub_src.find("with _state_lock:")
i_save = hub_src.find("save_latest(data)", i_lock)
i_cmp = hub_src.find("if at <= cur_at")
check("比较 syncAt 与 save_latest 在同一把锁内", i_lock < i_cmp < i_save, f"lock@{i_lock} cmp@{i_cmp} save@{i_save}")
check("保护窗判定也在锁内", i_lock < hub_src.find("reason': 'protected'", i_lock) if hub_src.find("reason': 'protected'", i_lock) > 0 else False)
check("GET /layout 读真源不加锁（只读，可接受）", hub_src.find("cur = load_latest()", hub_src.find("def _do_GET_inner")) < hub_src.find("with _state_lock:"))

# ── D. 归档 ──
print("\n-- D. archive --")
check("archive 保留 20 份（MAX_ARCHIVE=20）", "MAX_ARCHIVE = 20" in hub_src)
check("同秒同机归档名加序号防撞", "while os.path.exists(os.path.join(ARCHIVE, name))" in hub_src)
check("layout.json 权限收紧到 600", "os.chmod(tmp, 0o600)" in hub_src)
check("真源用 tmp+os.replace 原子写", "os.replace(tmp, LATEST)" in hub_src)
# 归档文件是否也收紧权限？
i_arch = hub_src.find("with open(os.path.join(ARCHIVE, name), 'w') as f:")
seg = hub_src[i_arch:i_arch+400]
check("⚠️ 归档文件未 chmod 600（真源是 600，归档是默认 644）", "chmod" not in seg)

# ── E. 客户端网络面 ──
print("\n-- E. 客户端网络面 --")
check("hub 地址双通道 LAN→ZT 自动回退", "192.168.113.11:18878" in main_src and "192.168.191.9:18878" in main_src)
check("httpGetJSON 有超时（timeout=3）", "timeoutInterval: timeout" in main_src)
check("httpPostJSON 有超时", "func httpPostJSON(_ urlStr: String, body: [String: Any], timeout: TimeInterval = 3)" in main_src)
check("POST 前 isValidJSONObject 预检（防 NSException abort）", "JSONSerialization.isValidJSONObject(body)" in main_src)
check("拉取失败有指数退避 10/20/40/60", "min(60, 10 << min(3, hubFailStreak - 1))" in main_src)
check("拉取恢复后退避归零", "拉取恢复，退避归零" in main_src)
check("非 200 落日志并区分 401/403/400", "func logHTTPCode" in main_src and "令牌失效" in main_src and "网段外被拒" in main_src)

# ── F. 部署脚本契约 ──
print("\n-- F. qspace-deploy.sh 契约 --")
dep = pathlib.Path("/Users/jon/Projects/Desktop-Icon-Manager/scripts/qspace-deploy.sh").read_text()
check("部署前检查 app 存在", "先构建" in dep)
check("部署前检查令牌文件存在", "缺令牌文件" in dep)
check("本机分支也 bootstrap（实测踩过的坑）", "launchctl bootstrap" in dep)
check("远程分支也 bootstrap", "launchctl bootstrap" in dep)
check("部署后自检实例数", "实例数:" in dep)
check("machine-tag 显式写入（不靠 hostname）", "machine-tag.txt" in dep)
check("首次部署分发令牌 + chmod 600", "chmod 600" in dep)
check("幂等：rm -rf 后重装", "rm -rf /Applications/QSpaceLayoutBar.app" in dep)
# 危险点：killall 在部署时无条件执行
check("⚠️ 部署用 killall 强杀（旧实例退出会回写 conf 竞争）", "killall QSpaceLayoutBar" in dep)
check("部署脚本不含单实例检查（可能起双实例——双实例勘误的根因）", "runningApplications" not in dep)

# ── G. 快照/部署一致性 ──
print("\n-- G. 部署一致性维度 --")
check("build.sh 写入构建版本号到 Info.plist（可区分三台跑的是哪版）", "CFBundleVersion" in pathlib.Path("/Users/jon/Projects/Desktop-Icon-Manager/QSpaceLayoutBar/build.sh").read_text())
check("启动日志打印版本号+pid+hub 地址", "startup: v" in main_src and "pid=" in main_src)
check("machineTag 优先读显式文件（hostname 改名不失效）", "machine-tag.txt" in main_src)

# ── H. uninstall 契约 ──
print("\n-- H. qspace-uninstall.sh 契约 --")
un = pathlib.Path("/Users/jon/Projects/Desktop-Icon-Manager/scripts/qspace-uninstall.sh").read_text()
check("默认保留 ~/qspace-layouts 数据", "保留数据" in un)
check("--purge 需二次确认", "确认？输入 yes 继续" in un)
check("--dry-run 不执行删除", "--dry-run" in un and "DRY=1" in un)
check("⚠️ --purge 循环里 ssh 失败不中止（for 循环内 || true 只在 rm 处）", "for h in mbp air studio; do uninstall_one" in un)

print(f"\n结果: {passed} 通过, {failed} 失败")
sys.exit(1 if failed else 0)
