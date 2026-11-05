# QSpace 桌面布局跨机同步

> 三台 Mac 的桌面图标布局**自动同步**。谁最后改动，其他机器 10 秒内跟着变。
> 中枢跑在常开的 MINIPC 上，支持局域网 + ZeroTier 双网络。

---

## 一、这是什么

macOS 的桌面图标位置存在 QSpace Pro 的私有配置里（`desktop.conf`），换台机器就是另一套位置。
本项目做三件事：

1. **布局跨机同步** —— 任意一台拖动图标，其余机器自动跟着排好
2. **屏幕自适应** —— 三台机器屏幕不同（内屏/外接/不同分辨率），用「数格子」相对坐标换算，不是死记像素
3. **中枢唯一真源** —— 布局只保存一份，不存在"哪台是权威机"的问题

### 为什么需要它

| 痛点 | 解决 |
|---|---|
| 换台电脑桌面全乱，找不到文件 | 布局自动跟随，打开就是熟悉的排列 |
| 合盖/开盖换屏幕后图标重叠、歪斜 | 三套屏幕参数写死进软件，切套时参数+坐标一起写 |
| 多台互相覆盖，改着改着更乱 | 中枢「后改赢」+ 60 秒吸收期，杜绝互相冲撞 |
| 笔记本带出门连不上家里中枢 | LAN 失败自动切 ZeroTier |

---

## 二、架构

```
        ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
        │  MacBook Pro │   │  MacBook Air │   │  Mac Studio  │
        │  客户端      │   │  客户端      │   │  客户端      │
        └──────┬──────┘   └──────┬──────┘   └──────┬──────┘
               │                 │                 │
       LAN 192.168.113.x    （失败自动切）    ZT 192.168.191.x
               │                 │                 │
               └────────┬────────┴────────┬────────┘
                        ▼
              ┌──────────────────────┐
              │  中枢 MINIPC:18878   │
              │  hub-server.py       │
              │  layout.json 唯一真源 │
              │  archive/ 保留 20 份  │
              └──────────────────────┘
```

**三台完全对等**，没有权威机。中枢只做一件事：保存时间戳最新的那份布局。

### 同步节奏

| 机制 | 时长 | 作用 |
|---|---|---|
| 同步轮询 | **10 秒** | 广播本机改动 + 拉取远端 |
| 指纹轮询 | 30 秒 | 检测本机布局变化 |
| **拖动保护期** | **10 秒** | 拖完 10 秒内不应用远端，防止刚拖的图标被弹回 |
| **吸收期** | **60 秒** | 应用远端后 QSpace 自己重排不算用户改动，防止互相覆盖 |
| 切套冷静期 | 20 秒 | 开合盖换屏幕后不把反推的歪坐标当真源 |
| 网络退避 | 10→20→40→60 秒 | 中枢不可达时指数退避，恢复后自动归零 |

---

## 三、核心设计

### 1. 数格子，不是记像素

桌面被抽象成网格，每个图标只记录 `(列, 行)`。跨机同步时按屏幕参数重新换算成像素。

**三套写死的屏幕参数**（实测标定，`QSpaceLayoutBar/main.swift` → `SPEC_A/B/C`）：

| 套 | 对应屏幕 | lod | 列距 | 行距 | 原点 | 网格 |
|---|---|---|---|---|---|---|
| A | Air 内屏 1710×1073 | 64 | 79 | 114 | 80,44 | 21×9 |
| B | MBP 内屏 1728×1084 | 64 | 90 | 114 | 53,43 | 19×9 |
| C | 外接/Studio 2560×1440 | 96 | 113 | 148 | 120,40 | 22×9 |

选哪套看**图标最多的那块屏**（不是面积最大的），避免双屏态用外接参数挤爆内屏。

### 2. 一套布局，三套映射

中枢只存一份 `(列,行)`。每台机器应用时用自己的套换算成像素，所以 Air 的 20 列和 Studio 的 21 列能表达同一个相对位置。

### 3. 中枢安全：三层门禁

| 层 | 机制 |
|---|---|
| 网段白名单 | 仅 `127.0.0.1` / `192.168.113.0/24` / `192.168.191.0/24` |
| 共享密钥 | `X-Hub-Token` 头 + `hmac.compare_digest` 恒定时间比较 |
| 数据校验 | body ≤1MB、items 非空且 ≤500 项、`name:str` + `col/row:int 1~1000`、时间戳不许超前 5 分钟 |

> 为什么要密钥：MINIPC 上跑着多个 Docker 容器，**host 网络模式的容器源 IP = 宿主 IP**，光靠网段白名单挡不住。

### 4. 客户端自检，不上报脏数据

上报前检查：格子越界？同格重叠？指纹为空？——任一不满足就拒绝上报，避免污染中枢。
应用后校验：重新读 conf 算一遍，比对期望位置，不一致就记日志告警。

---

## 四、目录结构

```
Desktop-Icon-Manager/
├── README.md                    ← 本文
├── QSpaceLayoutBar/             ← 客户端（菜单栏 app）
│   ├── main.swift               主程序（1667 行）
│   ├── Info.plist               bundle 配置（含 NSLocalNetworkUsageDescription）
│   └── build.sh                 构建脚本 → build/QSpaceLayoutBar.app
├── hub/                         ← 中枢（跑在 MINIPC）
│   ├── hub-server.py            HTTP 服务（:18878，systemd 自启）
│   └── qspace-hub.service       systemd 单元
├── scripts/                     ← 运维脚本
│   ├── qspace-deploy.sh         一键部署（all / 单台 / new <别名>）
│   ├── qspace-rollback.sh       布局回滚（列表 / --check / --local / 编号）
│   ├── qspace-uninstall.sh      完全卸载（--dry-run / --purge）
│   └── check-layout.py          独立验证（越界/重叠/列位/行距）
└── docs/
    ├── ARCHITECTURE.md          完整架构与设计决策
    └── audits/                  六轮 30 项白纸审计报告（共 64 项发现）
```

---

## 五、快速开始

### 前置

- macOS（三台已实测：MBP M4 Pro / Air / Mac Studio）
- [QSpace Pro](https://qspace.awehunt.com/)（本项目操作它的 `desktop.conf`）
- 一台常开机器跑中枢（任意 Linux / macOS，需 Python 3）
- SSH 免密到各台（`BatchMode=yes`）

### 1. 构建客户端

```bash
cd QSpaceLayoutBar
./build.sh
# 产物：build/QSpaceLayoutBar.app
```

### 2. 部署中枢（MINIPC 或任意常开机器）

```bash
# 生成共享密钥（600 权限）
mkdir -p ~/qspace-hub ~/qspace-layouts
openssl rand -hex 32 > ~/qspace-layouts/hub-token.txt
chmod 600 ~/qspace-layouts/hub-token.txt

# 装中枢
scp hub/hub-server.py  <中枢机>:~/qspace-hub/hub.py
scp ~/qspace-layouts/hub-token.txt <中枢机>:~/qspace-hub/hub-token.txt
scp hub/qspace-hub.service <中枢机>:/tmp/
ssh <中枢机> 'sudo cp /tmp/qspace-hub.service /etc/systemd/system/ && \
              sudo systemctl daemon-reload && \
              sudo systemctl enable --now qspace-hub && \
              systemctl is-active qspace-hub'

# 验证
curl -s -H "X-Hub-Token: $(cat ~/qspace-layouts/hub-token.txt)" \
     http://<中枢IP>:18878/status
```

### 3. 部署客户端到各台

```bash
# 首次部署新机器（自动建目录 + 分发令牌 + 装 launchd）
./scripts/qspace-deploy.sh new mbp
./scripts/qspace-deploy.sh new air
./scripts/qspace-deploy.sh new studio

# 日常更新（改了代码后）
./scripts/qspace-deploy.sh all
```

### 4. 验证

```bash
python3 scripts/check-layout.py
# 期望：PASS 无越界/无同格重叠/列位标准/行距足够
```

---

## 六、日常运维

### 看状态

```bash
./scripts/qspace-rollback.sh --check      # 中枢当前布局（谁上报的/几个图标）
./scripts/qspace-rollback.sh              # 归档列表（标 ✅/❌ 可用性）
tail -f ~/qspace-layouts/app.log          # 客户端日志
ssh <中枢机> 'tail -f ~/qspace-hub/hub.log'   # 中枢日志
```

### 回滚到历史布局

```bash
./scripts/qspace-rollback.sh          # 列出归档（1 = 最新，✅ = 可用）
./scripts/qspace-rollback.sh 3        # 回滚到第 3 个，三台 10~20 秒自动应用
./scripts/qspace-rollback.sh --local  # 本地手动快照（另一套来源）
```

回滚会写 `~/qspace-layouts/rollback.log`（时间/编号/文件/操作者）。

### 完全卸载

```bash
./scripts/qspace-uninstall.sh --dry-run   # 先看会删什么
./scripts/qspace-uninstall.sh all          # 卸三台（保留数据）
./scripts/qspace-uninstall.sh --purge      # 连中枢+数据一起删（需输入 yes）
```

### 故障排查

| 现象 | 查什么 |
|---|---|
| 不同步 | `grep 'hub:' ~/qspace-layouts/app.log` — 看是 401（令牌）/ 403（网段）/ 超时 |
| 图标重叠 | `python3 scripts/check-layout.py` — 看是越界还是同格重叠 |
| 刚拖完又被弹回 | 防抖 10 秒内不该应用远端；若发生说明 `lastLocalChangeAt` 没刷新 |
| 开合盖乱位 | 切套冷静期 20 秒；查日志 `屏幕切换 X → Y` |
| 中枢 500 | `ssh <中枢机> 'tail -50 ~/qspace-hub/hub.log'` — 看 `Unhandled exception` |

### 手动改布局后想立刻生效

不用管——拖动保护期 10 秒后自动上报。想立刻推：重启客户端（`launchctl kickstart -k gui/$(id -u)/com.jon1131120.qspacelayoutbar`）。

---

## 七、中枢协议

```http
POST /layout
X-Hub-Token: <共享密钥>
Content-Type: application/json

{
  "syncAt": 1793817197,          // 秒级时间戳（后改赢）
  "sourceMachine": "Studio",
  "layoutFingerprint": "5b8cbf1cbeb6e7b",
  "specKey": "C",                 // 上报方用的哪套
  "items": [
    {"name": "样片", "col": 22, "row": 6}     // 只存文件名，不存完整路径
  ]
}
```

**接受条件**：`syncAt` 大于现有值。否则返回 `{"accepted": false, "reason": "older"}`。

```http
GET /layout   → 当前布局（同门禁）
GET /status   → {ok, hasLayout, syncAt, sourceMachine, iconCount, archiveCount, serverTime}
```

**存储**：
- `~/qspace-hub/layout.json` —— 唯一真源（600 权限）
- `~/qspace-hub/archive/YYYYMMDD-HHMMSS-机器.json` —— 保留最近 20 份
- `~/qspace-hub/hub.log` —— 5MB × 3 轮转

---

## 八、设计决策（为什么这样做）

| 决定 | 原因 |
|---|---|
| 中枢放常开机器，不做权威机 | 用户原话「我 Air 也不是 24 小时开机的呀」——任何一台都可能关机 |
| 只存文件名不存完整路径 | 三台桌面根路径相同，文件名足够定位；避免泄露目录结构 |
| 三套参数写死进代码 | 从坐标反推步长会有误差，实测导致重叠；标定一次写死 |
| 按图标最多的屏选套 | 双屏态按面积选会选中外接屏，把内屏挤爆 |
| 60 秒吸收期 | QSpace 应用新布局后会自己重排，若不吸收会被当成用户改动→覆盖中枢→死循环 |
| 网段白名单 + 共享密钥 | Docker host 网络容器源 IP = 宿主 IP，白名单挡不住 |
| 直连 HTTP 而非 SSH | SSH 每条命令要 fork 进程，10 秒轮询代价高；HTTP 走本地网络 TCC 静默放行 |

---

## 九、已验证的事实

- **三台二进制一致**：md5 相同，同一次构建部署
- **映射精度 0 失真**：79/90/113 步长格 ↔ 像素往返无损，标定 40 点 120/120 吻合
- **端到端同步**：Studio 拖 1 个图标 → MBP/Air 5 秒内 `moved=1 校验 45/45`
- **收敛稳定**：同步后 75 秒中枢零上报（不抖动）
- **灾难恢复**：MINIPC 整机丢失不影响使用，三台各自有完整布局副本，任一台重新上报即可重建中枢

详细验证记录见 `docs/audits/`（六轮白纸审计，累计 64 项发现全部修复）。

---

## 十、来源与许可

早期版本 fork 自 [Desktop Icon Manager](https://github.com/parker9/DIM)（MIT）。
当前代码已完全重写为面向 QSpace Pro 的跨机同步方案，上游源码不再包含在本仓库中（需要请查 git 历史）。

本项目为个人自用工具，按现状提供，不保证适用其他环境。
