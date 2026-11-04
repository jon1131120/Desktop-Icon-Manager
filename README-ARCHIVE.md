# 归档说明

本目录存放**历史遗留、当前不再使用**的源码，保留以便追溯，可随时恢复。

| 目录 | 是什么 | 为什么归档 |
|---|---|---|
| `DIM/` | 上游 Desktop Icon Manager 的完整 Swift 源码（本项目从它 fork 而来） | 当前只用 `QSpaceLayoutBar/`（自研菜单栏 app）。DIM 原版已装在机器上但从未使用（`kMDItemLastUsedDate = null`），已于 2026-11-05 移到 `~/Applications-backup/DIM.app.disabled-20261105` |
| `DIMHelper/` | DIM 的辅助进程（菜单栏图标 + 辅助功能监听） | 同上 |
| `qspace-layout/` | 早期的 Python 版布局管理器（`qspace_layout.py`，save/restore/list/delete/show） | 已被 Swift 版完全取代，功能重叠 |

## 恢复方式

```bash
cd ~/Projects/Desktop-Icon-Manager
git mv archive/DIM DIM
git mv archive/DIMHelper DIMHelper
git mv archive/qspace-layout qspace-layout
```

## 当前唯一在用的部分

- `QSpaceLayoutBar/main.swift` — 菜单栏 app（本项目主体）
- `QSpaceLayoutBar/build.sh` / `Info.plist` — 构建与打包
- 部署产物：`/Applications/QSpaceLayoutBar.app`（三台 Mac）
- 中枢：MINIPC `~/qspace-hub/hub.py`（:18878，systemd 自启）
- 运维脚本：`~/qspace-layouts/`（check-layout.py / qspace-rollback.sh / hub-server.py）
