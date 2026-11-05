# 三 Mac 桌面布局同步 · v5 中枢架构

> 最后校准：2026-11-06。**唯一真源是代码**：`QSpaceLayoutBar/main.swift` 的 `SPEC_A/B/C`
> 与 `hub-server.py`。本文若与代码冲突，以代码为准并回来改本文。

## 一句话

MINIPC 上的中枢（`:18878`）保存唯一一份布局；三台 Mac 每 10 秒轮询，本机布局变了就上报，
中枢按 `syncAt` 后改赢，其余两台拉到后按自己的屏幕套重新换算像素。

## 架构

```
 MacBook Pro ─┐                    ┌─ LAN 192.168.113.x（优先）
 MacBook Air ─┼── 客户端 ──────────┤
 Mac Studio  ─┘                    └─ ZT  192.168.191.x（LAN 失败自动切）
                        ↓
        MINIPC 中枢 hub-server.py :18878
        layout.json（唯一真源）+ archive/（保留 20 份）
```

三台**完全对等**，没有权威机（任何一台都可能关机/带出门）。

## 三套屏幕参数（写死，应用时参数+位置一起写）

| 套 | 屏幕 | lod | 列距 | 行距 | 原点 | 网格 |
|---|---|---|---|---|---|---|
| A | Air 内屏 1710×1073 | 64 | 79 | 114 | 80,44 | **21**×9 |
| B | MBP 内屏 1728×1084 | 64 | 90 | 114 | 53,43 | **19**×9 |
| C | 外接 / Studio 2560×1440 | 96 | 113 | 148 | 120,40 | **22**×9 |

选套规则：**按「图标最多的那块屏」**（不是面积最大），否则双屏态会用外接参数挤爆内屏。

## 同步节奏（实测值）

| 机制 | 时长 | 作用 |
|---|---|---|
| 同步轮询 | 10 秒 | 切套 → 重叠修复 → 广播 → 拉取 → 应用 |
| 指纹轮询 | 30 秒 | 检测本机变化 + 刷新菜单 |
| 拖动防抖 | 10 秒 | 停手 10 秒才上报，且期内不应用远端 |
| 吸收期 | 60 秒 | 应用远端后 QSpace 自行重排不算用户改动 |
| 切套冷静期 | 20 秒 | 开合盖后不把反推的歪坐标当真源 |
| 网络退避 | 10→20→40→60 秒封顶 | 中枢不可达时指数退避，恢复后归零 |
| GET 日志节流 | 5 分钟/来源 | 否则每天 2.6MB 噪声 |

## 数据格式

```json
POST /layout   头: X-Hub-Token: <共享密钥>
{
  "syncAt": 1793912026,          // 秒级，后改赢
  "sourceMachine": "Studio",
  "layoutFingerprint": "dcab6a889ee8edf6",
  "specKey": "C",
  "items": [{"name": "样片", "col": 22, "row": 6}]   // 只存文件名，不存路径
}
```

`GET /layout`、`GET /status` 同门禁。返回 `accepted:false, reason:"older"` 表示中枢有更新的版本。

## 中枢三层门禁

| 层 | 规则 |
|---|---|
| 网段白名单 | `127.0.0.1/32` / `192.168.113.0/24` / `192.168.191.0/24` → 否则 403 |
| 共享密钥 | `X-Hub-Token` + `hmac.compare_digest` → 否则 401 |
| 数据校验 | body ≤1MB / items 非空且 ≤500 / `name:str` + `col,row:int 1~1000` / 时间戳不超前 300s / 元素结构逐项校验 |

> 为什么需要密钥：MINIPC 上跑着多个 Docker 容器，**host 网络模式容器源 IP = 宿主 IP**，
> 光靠网段白名单挡不住（实测容器内可 POST 200）。

## 硬坑清单（按类型，改代码前必读）

1. **macOS 本地网络 TCC 静默拦截** — 缺 `NSLocalNetworkUsageDescription` 时系统不弹窗、不写日志，
   直连 IP 的请求直接失败。补键后静默放行，**不需要用户授权**。
2. **Docker host 网络绕过 IP 白名单** — 见上，必须叠共享密钥。`curl --interface` 测不出这个问题
   （源 IP 不变），要从真容器内发起才能复现。
3. **互相同步覆盖** — 应用远端后 QSpace 会自行重排，若当成用户改动上报 → 覆盖中枢 → 三台死循环。
   用 **60 秒吸收期** 化解（期内只更新基线不上报）。
4. **时钟偏差** — 落后：`at <= cur_at` 永远被拒 → `clockOffset` 补偿（`latestSyncAt+1-now`）；
   超前 >300s：400 → `hubServerTime()` 校准重发**并同步更新 offset**（否则每轮都 400）。
5. **同秒碰撞** — 两台同秒上报只有第一个被接受。被拒后 `pendingRepushFp` 下轮重推、
   本轮 `skipPullOnce` 不拉远端（否则远端会盖掉本机刚改的）。
6. **回滚编号错位** — 列表与回滚**必须共用同一个选择器**（`select_archive`）。
   曾用两套排序规则 → 用户看到 #1 是 A，实际回滚到 B。
7. **状态文件非原子写** — `sync-state.json` 被截断 → 状态全丢 → 重启后把旧布局当新改动。
   用 `tmp + replaceItemAt`。
8. **假开关** — 菜单写 `microNudgeOff`、代码读 `microNudgeOn` → 点了重启即失效。
   写入的键必须与读取的键同名。
9. **中文全角括号紧跟变量名** — `$H（首次` 被 bash 当成变量名 → 空值。永远用 `${H}`。
10. **重叠自动推开只修「真同格」** — 把"撞禁区"也算推开理由会连锁改动用户布局
    （实测一次误推 7 个）。且连续修 3 次仍重叠就停手告警（防 QSpace 重启后又放重 → 死循环）。
11. **`applyingRemote` 必须 defer 复位** — 中途 return 会永久卡 true，
    表现为"同步完全不工作但日志无报错"（最难查的一类）。
12. **fuzz 测试会把中枢打脏** — 测试必须用**过去**的 `syncAt`（走 `older` 拒绝路径），
    否则正常请求会被接受并覆盖真源。

## 运维命令

```bash
# 部署（改完代码）
./scripts/qspace-deploy.sh all            # 三台
./scripts/qspace-deploy.sh new <ssh别名>  # 全新机器首次（建目录+发令牌+装plist+bootstrap）

# 验证
python3 scripts/check-layout.py           # 期望 PASS 无越界/无同格重叠/列位标准/行距足够

# 回滚
./scripts/qspace-rollback.sh              # 列表（✅=可用，标注项数与 fp）
./scripts/qspace-rollback.sh 3            # 回滚到第 3 个，三台 10~20 秒自动应用
./scripts/qspace-rollback.sh --local      # 本地手动快照

# 状态
./scripts/qspace-rollback.sh --check      # 中枢当前布局（谁上报/几项）
tail -f ~/qspace-layouts/app.log          # 客户端（本地时区）
ssh minipc 'tail -f ~/qspace-hub/hub.log' # 中枢

# 中枢维护
ssh minipc 'sudo -n systemctl restart qspace-hub'
ssh minipc 'ls -t ~/qspace-hub/archive | head'

# 卸载
./scripts/qspace-uninstall.sh --dry-run   # 先看会删什么
./scripts/qspace-uninstall.sh all
```

## 仓库结构

`jon1131120/desktop-icon-suite`（backup）+ `jon1131120/Desktop-Icon-Manager`（fork），两者 main 同步。

```
QSpaceLayoutBar/   客户端 main.swift + Info.plist + build.sh
hub/               中枢 hub-server.py + qspace-hub.service
scripts/           deploy / rollback / uninstall / check-layout.py
docs/              ARCHITECTURE.md + audits/问题总台账.md
```

**自包含铁律**：改完必须验证「全新 clone → build → md5 == 三台部署版」。

## 关键文件

| 路径 | 作用 |
|---|---|
| `~/Library/Application Support/com.jinghaoshe.qspace.pro/desktop.conf` | QSpace 布局真源（我们读改写） |
| `~/qspace-layouts/hub-token.txt` | 共享密钥（600） |
| `~/qspace-layouts/sync-state.json` | 客户端同步状态（原子写） |
| `~/qspace-layouts/app.log` | 客户端日志（5MB 轮转） |
| `~/qspace-layouts/archive-old/` | 手动快照（`--local` 用） |
| MINIPC `~/qspace-hub/layout.json` | 中枢唯一真源（600） |
| MINIPC `~/qspace-hub/archive/` | 归档 20 份 |
