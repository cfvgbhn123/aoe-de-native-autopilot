# Validation status / 验证状态

Snapshot / 源码快照：2026-10-13。Release / 发布标记：`0.1.0-alpha.1`。Bridge protocol / 桥接协议：`6`。

| Area / 项目 | Evidence / 证据 | Scope / 范围 |
| --- | --- | --- |
| Python controller, supervisor, diagnostics / Python控制器、监督器、诊断 | 47 offline tests / 47项离线测试 | Identity checks, bounded recovery, manual stop, terminal state, resource parsing / 身份检查、有限恢复、人工停止、终局、资源解析 |
| C++ bridge fixtures / 原生夹具 | Actual bridge source compiled and exercised / 编译并运行实际桥接源码 | Lock/release concurrency, destructor, foreign AI rejection, computer flag restoration, narrow builder cancellation, fog/bounds resource replay / 锁与恢复、析构、外来AI拒绝、标记恢复、窄条件取消、雾与边界资源补全 |
| v5 live takeover / v5实机 | New-match samples, on/off, watchdog expiry, 60-second run / 新局采样、启停、超时、60秒运行 | Computer flag=1 during control and restored to0; earlier permanent construction waits absent / 接管标记1且恢复为0，先前永久建造等待未再出现 |
| v6 historical resource replay / v6历史资源补全 | Offline native fixtures only / 仅离线原生夹具 | **Live validation pending / 实机验证待完成** |
| Real save/load/match change; full game; hours-long operation / 实际读档换局、完整胜负、数小时运行 | Not completed / 未完成 | No claim of full stability / 不宣称整局稳定性 |

## Diagnosis / 诊断依据

The original takeover changed the player control type and AI pointer but omitted the computer-AI event flag at `player+0x354`. Native game initialization sets that flag. Construction/gathering completion paths inspect it. Old-match samples contained permanently waiting Build actions with type101/state16/no target/retry0, even though villagers were registered in AI lists.

旧接管遗漏 `player+0x354` 电脑AI标记，导致部分建造/采集通知进入人工路径。旧局中村民已登记，但存在 type101/state16/目标为空/重试为零的永久建造等待。v5 保存、设置、恢复标记，并使用引擎取消入口解除窄条件坏任务。

Recreating AI also drops its historical resource tables: native incremental discovery consumes only newly explored tiles. A live v5 reinitialization sample showed empty known wood/stone/gold tables and many villagers without actions. Protocol6 replays revealed Gaia resources through the native discovery function, respecting current visibility and the static-object condition for explored fog tiles. Native fixture coverage includes unseen resources, fogged moving objects, visible objects and coordinate bounds. Live effectiveness remains pending.

AI重建还会丢失历史资源表；原版只处理新探索区域。v5重建后的采样出现木材/石料/黄金认知为空、大量村民无动作。协议6按原版可见性和雾中静态对象条件补已揭示资源。夹具覆盖未探索资源、雾中移动对象、可见对象及边界，实机效果仍待验证。

## Remaining work / 待办

1. Verify resource tables, valid gathering targets and actual resource delivery after v6 takeover/reinitialization / 验证v6接管与重建后的资源表、采集目标与交回资源。
2. Verify historical exploration-map state and scouting / 验证内部探索图与侦察行为。
3. Resolve the strategy-loader water/land classification argument using its original configuration call path / 按原版配置调用路径修正水陆参数。
4. Exercise save/load, match replacement, full outcomes and extended operation / 完成读档换局、整局胜负与长时验收。

Raw local process addresses, PID records, dumps and machine paths are excluded from this distribution / 发布包不包含本机进程地址、PID记录、转储和个人路径。No game was launched or modified to validate the extracted package / 独立包验证未启动或修改实际游戏。
