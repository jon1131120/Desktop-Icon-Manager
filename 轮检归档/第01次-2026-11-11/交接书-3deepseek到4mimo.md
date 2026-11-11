# 交接书：第 3 位（deepseek-flash）→ 第 4 位（mimo-v2.6-flash）

## 项目主链路（不变）
QSpace 桌面图标跨机同步：Swift 菜单栏客户端 ⇄ Python 中枢（MINIPC :18878）。基线 git 4239482（VIP 三格体系 + 前两位全部修复已落实/已提交/已推送双仓）。

## 前两位+主会话的关键结论（你的复核基线）
- 第 1 位 MiniMax：30 轮+15 工况，11 patch 已应用（rollback P0/部署校验/参数自愈/屏号自愈等）
- 第 2 位 glm：复核专区 14 项（抓出第 1 位 3 真问题+主会话设施 1 P1）；P1-P7 patch 损坏由主会话按意图手工落实（两段式预登记/屏号自愈 v5/test-hub 显式报错/deploy 容错目标）
- 第 3 位 deepseek：15 分钟闪电完成（校验器 PASS/复核 20 项/109 证据 0 越界），台账 30 行质量高，主链 15 核心全绿（第 1-15 轮），新发现见下

## 第 3 位 deepseek 报的未修问题（本轮可复核或补充实测）
1. 中枢 syncAt="abc"（类型错误）返回 500，应 400（输入校验缺口）
2. qspace-uninstall.sh 的 all 模式 rm -rf ~/qspace-hub 无确认（危险操作）
3. deploy.sh 0 实例时 grep -c + set -e 静默中止——glm 的 P5 patch 同样损坏未落实，**此问题仍在**，主会话计划手工修但尚未动手（你可实测确认后给修复 patch）
4. 中枢 layout.json 被清空后客户端不自愈（第 14 轮发现：GET 200 返回空对象）
5. clamp 上界无测试覆盖（变异 M2 不变红——selftest 增强项）
6. 跨套链式映射 C→A→B 两跳 ≠ C→B 一跳（第 27 轮：映射区有差异——链式不等价的量化，值得深挖）
7. body>1MB 返回 413 非 400（合同口径统一问题）

## ⚠️ 实测教训（必读，前三位用血泪换的）
1. 生产 conf 无 screenWidth/screenHeight 字段——依赖尺寸字段的判据永不触发；fixture 必须真 conf 采样
2. 卷宗名带尾随空格（/Volumes/Jon113␣␣）——匹配必须 trim+前缀
3. isSingleScreenConf 按 screen 值分组——失真 conf 永远判非单屏，不能当屏号自愈门卫
4. 沙箱必须用 /tmp/build_sandbox.sh（QSPACE_HUB_DIR 真隔离）；裸起 hub-server.py 会打到生产（假隔离 P0 事故，见第 1 位交接书）
5. 真机零触碰照旧；**Studio 正跑杰哥任务，完全不许动**

## 你的特殊注意事项（小米系历史风险）
历史轮检中小米模型读到「删除/覆盖/不可逆」类代码会被供应商安全过滤拦截（safety filter refused）。
本项目检查内容含卸载脚本 rm -rf、rollback 强制覆盖等——**如果被拦，如实记录到进度.log 后继续
其他轮次，不要卡死在一个维度上**；涉删除类脚本的审查可以改为「静态描述+沙箱只读验证」。
