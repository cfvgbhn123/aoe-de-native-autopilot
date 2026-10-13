# AoE DE Native Autopilot

**中文 / English · Windows x64 · Experimental / 实验性 · MIT**

让《帝国时代 I：决定版》的原版极难 AI 接管本地玩家，提供可恢复的控制台、持续监督与经济诊断工具。

Let the game's built-in hardest AI take over the local player in **Age of Empires I: Definitive Edition**, with manual recovery, a match supervisor, and economy diagnostics.

> 当前源码版本：`0.1.0-alpha.1`，桥接协议 `6`。v5 的新局采集、启停与看门狗已有本机实测；v6 的历史资源认知补全已通过离线原生夹具，**尚未完成实机验收**。这是研究用途的 alpha 源码项目。
>
> Source version: `0.1.0-alpha.1`, bridge protocol `6`. Local live tests covered v5 gathering, takeover/release and watchdog recovery. The v6 historical resource discovery repair passed native fixtures but **has not yet been validated in a live match**. This is an experimental alpha source project.

## 中文说明

### 项目用途

这个项目调用游戏已有的 AI 构造、策略装载和更新流程。具体文明与地图策略由原版游戏选择，它不是独立训练的模型，也不包含游戏自己的 AI 脚本。

- 开启/关闭原版极难 AI 接管，恢复人工控制。
- 校验游戏文件指纹、运行时签名、进程创建时间和对局身份。
- 暂停时可恢复人工；控制器失去心跳后由原生看门狗恢复。
- 持续监督当前对局，按固定间隔记录状态；仅在同局、明确心跳过期且安全条件满足时有限恢复。
- 只读检查村民动作、采集目标、实际/期望资源分配和已知资源。
- 修复电脑 AI 事件标记遗漏，并在开启时解除已确认的永久等待建造动作。
- v6 在模拟线程通过原版可见性和发现对象入口，补回已揭示的 Gaia 资源认知；未探索区域不补入。

### 兼容范围与要求

| 项目 | 要求 |
| --- | --- |
| 游戏 | **帝国时代 I：决定版**，Steam Build **97381**（2023-11-10），`AoEDE_s.exe` |
| 游戏 SHA-256 | `06a3e142b526741a8c23390a2a4088060f41e34b5715cd0b4f2ad442e9c8506a` |
| 系统 / Python | Windows x64，64 位 Python **3.10+**；当前本地验证使用 3.11 |
| 原生构建 | MSVC x64 C++ 工具与 Windows 10/11 SDK |
| 对局 | 单机随机地图或死斗，标准或征服胜利条件 |
| 运行依赖 | Python 标准库，无须安装第三方 Python 包 |

战役、联机、回放、编辑器测试、其他胜利条件和未适配版本会被拒绝。不同 Build 不能直接复用本项目的偏移。自行安装正版游戏与构建工具；源码包不提供它们。

### 快速开始

在本项目根目录打开 PowerShell：

```powershell
Copy-Item aoe_ai_config.example.json aoe_ai_config.json
# 编辑 aoe_ai_config.json，把 game_dir 改成自己的 AoEDE 安装目录。
python -X utf8 native/build_aoe.py
python -X utf8 -m aoe_autopilot --diagnose
```

先在游戏里进入受支持的单机对局，再双击 `launch_aoe_ai.cmd`，或执行：

```powershell
python -X utf8 -m aoe_autopilot
```

控制台启动时只连接。输入 `on` 开启接管，`off` 恢复人工，`status` 查看状态，`quit` 恢复人工并退出。如出现进程访问被拒绝，请让游戏与控制台以相同权限等级运行。

| 快捷键 | 普通控制台 | 持续监督器 |
| --- | --- | --- |
| Ctrl+Alt+F8 | 切换接管 | 恢复人工并停止 |
| Ctrl+Alt+F9 | 显示状态 | 保存即时状态 |
| Ctrl+Alt+F10 / Ctrl+Alt+Pause | 恢复人工 | 恢复人工并停止 |

首次开启会按需构建桥接，并校验源码/DLL 指纹及导出。游戏里已加载旧桥接或不同构建时，先退出并重新启动游戏。

### 持续接管与诊断

关闭普通控制台后启动监督器：

```powershell
python -X utf8 tools/supervise_match.py
```

它会连接并开启当前对局；默认每 0.2 秒校验、续心跳，每 15 秒保存 `aoe_ai_logs/supervisor_status.json` 和 `supervisor_events.jsonl`。同局的明确心跳超时最多恢复三次。主动停止、换局、读档和其他异常不会被当成重新开启授权。Ctrl+C 或表中的停止快捷键会恢复人工并退出。普通控制台与监督器共用互斥，不能同时持有接管。

下列工具只读取状态，不开启 AI，也不替代监督器的心跳：

```powershell
python -X utf8 tools/observe_match.py --samples 4 --interval 15 --output aoe_ai_logs/observation.json
python -X utf8 tools/inspect_economy.py --output aoe_ai_logs/economy.json
# 从 diagnose 的输出取得本次游戏 PID，再填写下方占位值。
python -X utf8 tools/inspect_villager_tasks.py --pid GAME_PID --out aoe_ai_logs/villagers.json
```

恢复人工后，已经发出的采集、移动、建造和生产命令仍可能继续执行。存档、读档、换局或返回菜单前，先 `off`，并推进一次模拟以完成对象回收；之后重新连接。不要反复开启来解决经济问题，每次重建会重置 AI 内部状态。

### 开发与验证

```powershell
python -X utf8 -m unittest discover -s tests -v
python -X utf8 tools/test_native_policy.py
```

当前提取包的验证记录见 [docs/VALIDATION.md](docs/VALIDATION.md)。实机恢复测试会改变当前对局；准备好测试对局后，才运行：

```powershell
python -X utf8 -u tools/verify_aoe_recovery.py --cycles 1 --soak 60
```

`--pause-tests` 会发送游戏暂停按键。实机工具结束时尝试恢复人工，不能在重要对局中把它当作只读诊断。

### 已知限制

- 原版极难 AI 可能获得资源奖励；初始化余额恢复不等于阻止之后的奖励。
- v6 资源认知补全仍需实机验证。内部探索图的历史恢复、侦察行为需要后续观察。
- 策略装载第六参数目前使用地图尺寸字段；原版调用来源是开局配置的水陆分类，正确活跃对局访问路径仍待确认。
- 真实存档/读档/换局、完整胜负及数小时稳定性尚未完成验收。
- 暂停恢复后立即更换世界可能使待回收对象留存到游戏进程退出。
- 原生 hook 与看门狗保留到游戏进程退出，不执行在线卸载。

### GitHub 仓库与后续贡献

公开源码仓库：<https://github.com/cfvgbhn123/aoe-de-native-autopilot>。克隆后可创建分支、提交修改并推送：

```powershell
git clone https://github.com/cfvgbhn123/aoe-de-native-autopilot.git
cd aoe-de-native-autopilot
git switch -c fix/short-description
# 修改代码后检查并提交
git status --short
git add <修改的文件>
git commit -m "Describe the change"
git push -u origin fix/short-description
```

然后在 GitHub 发起 Pull Request。`aoe_ai_config.json`、构建产物与日志已被忽略；提交前检查 `git status --short` 和暂存内容。

首次 GitHub Release 建议使用 `v0.1.0-alpha.1` 并标为 **Pre-release**，附源码 ZIP 和验证说明。不要把 alpha 描述为已经通过完整战局验收的稳定版。

### 许可证与贡献

项目原创源码采用 [MIT License](LICENSE)。游戏、商标、商业 AI 脚本和其他原厂素材由其权利人持有；这是社区研究工具，不是 Microsoft、Xbox Game Studios 或游戏开发商的官方项目。源码发布包只包含本项目代码和文档。

欢迎提交可复现的问题、版本适配与修复。请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，报告游戏 Build、模式、操作步骤和脱敏后的诊断摘要。

## English

### Purpose and features

This project invokes the game's existing AI constructor, strategy loader and update loop. The game selects the civilization/map strategy. It is not a trained external model and does not redistribute the game's AI scripts.

- Enable hardest native AI control, then restore manual control.
- Verify executable fingerprints, runtime signatures, process lifetime and match identity.
- Release control while paused; use a native watchdog when controller heartbeats expire.
- Supervise one match, persist samples, and recover from explicitly identified watchdog expiry under bounded conditions.
- Inspect villager actions, gathering targets, actual/desired resource allocation and known resources without taking control.
- Set and restore the native computer-AI event flag; cancel narrowly identified permanently waiting construction actions through the engine.
- Protocol 6 replays already revealed Gaia resources through native visibility/discovery functions on the simulation thread. Unexplored resources are excluded.

### Requirements and compatibility

Windows x64; 64-bit Python 3.10+; MSVC x64 C++ tools and a Windows 10/11 SDK. Runtime Python dependencies are standard-library only. Local tests used Python 3.11.

Only **Age of Empires I: Definitive Edition**, Steam **Build 97381**, is supported. The executable is `AoEDE_s.exe` with the SHA-256 shown in the compatibility table above. Supported matches are offline random maps or deathmatches with Standard or Conquest victory conditions. Campaigns, multiplayer, replays, editor tests, other victory conditions and unknown builds are rejected. Install your own game and build tools.

### Getting started

From the project root, copy `aoe_ai_config.example.json` to `aoe_ai_config.json`, then edit `game_dir` to your installation directory. Enter a supported offline match and run:

```powershell
python -X utf8 native/build_aoe.py
python -X utf8 -m aoe_autopilot --diagnose
python -X utf8 -m aoe_autopilot
```

Alternatively, double-click `launch_aoe_ai.cmd`. The console initially connects without enabling AI. Use `on`, `off`, `status` and `quit`. Ctrl+Alt+F8 toggles control, F9 displays status, and F10 or Pause restores manual control. If access is denied, run the game and controller at the same privilege level.

The first enable operation builds the bridge when necessary and verifies its fingerprint and exports. Restart the game if a previous bridge or a different build is already loaded.

### Supervision and diagnostics

Close the regular console and run `python -X utf8 tools/supervise_match.py`. It connects and enables AI for the current match, checks/renews every 0.2 seconds and records every 15 seconds. The supervisor permits at most three recoveries from explicit watchdog expiry in the same active match, after cleanup. User stops, loads, match changes and other errors do not authorize automatic re-enabling.

Ctrl+C, Ctrl+Alt+F8, Ctrl+Alt+F10 or Ctrl+Alt+Pause restores manual control and stops supervision. Ctrl+Alt+F9 records an immediate sample. The supervisor and console cannot both own control.

`observe_match.py`, `inspect_economy.py` and `inspect_villager_tasks.py` are read-only diagnostics; they neither enable AI nor maintain the controller's heartbeat. See the commands above and replace `GAME_PID` with the current process ID from `--diagnose`.

Release control before saving, loading, changing matches or returning to the menu, and let one simulation update complete object cleanup. Previously issued game commands can continue after release. Avoid repeated initialization as an economy workaround.

### Tests and limitations

Run `python -X utf8 -m unittest discover -s tests -v` and `python -X utf8 tools/test_native_policy.py`. See [validation status](docs/VALIDATION.md). `verify_aoe_recovery.py` changes the current match and attempts manual recovery when finished; `--pause-tests` sends the game's pause key.

Hardest native AI can receive resource bonuses. Protocol 6's resource discovery replay remains unverified in a live match. Historical exploration-map state and scouting require further observation. The strategy-loader map classification argument still needs correction based on the verified original call path. Real save/load transitions, full-match outcomes and hours-long stability remain unverified. Pending cleanup after a paused world replacement can persist until process exit; hooks/watchdogs remain loaded until then.

### GitHub repository and contributions

The public source repository is <https://github.com/cfvgbhn123/aoe-de-native-autopilot>. Clone it, create a feature branch, commit your change and push that branch before opening a Pull Request. Local config, build outputs and runtime logs are ignored; review staged files before committing.

Use `v0.1.0-alpha.1` for an initial **Pre-release**, with the source archive and validation notes. The original project code uses the [MIT License](LICENSE). Game assets, scripts and trademarks remain with their respective owners. This is an unofficial community research tool. Contributions and reproducible reports are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

## Project layout / 项目结构

```text
aoe-de-native-autopilot/
├── aoe_autopilot/                 Python controller, console, supervisor
├── game_common/                   Standalone Windows/process helpers
├── native/                        C++ bridge and local builder
├── tools/                         Diagnostics and validation entry points
├── tests/                         Offline Python tests and native fixtures
├── docs/VALIDATION.md              Validation evidence and limits
├── .github/                       CI and issue template
├── aoe_ai_config.example.json      Portable configuration template
├── launch_aoe_ai.cmd               Windows launcher
├── README.md                      中文 / English
├── CONTRIBUTING.md
└── LICENSE                        MIT
```
