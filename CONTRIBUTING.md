# Contributing / 贡献指南

## 中文

本项目处于 alpha 阶段。提交问题时提供游戏 Build、模式和胜利条件、源码版本、可复现步骤，以及已脱敏的诊断摘要。请区分“无动作”“目标丢失等待”“正常移动/交回资源”，不要仅凭单位数量判断 AI 健康。

修改版本相关偏移或引擎调用时，附对应版本的函数签名和调用依据。优先通过游戏原生入口维护对象生命周期；补齐恢复人工、暂停、超时、对局身份变化和外来 AI 拒绝路径。不要提交游戏 EXE/DLL、商业 AI 脚本、内存转储、个人路径或运行日志。

提交前在项目根目录准备本地配置，然后运行：

```powershell
Copy-Item aoe_ai_config.example.json aoe_ai_config.json
python -X utf8 -m unittest discover -s tests -v
python -X utf8 tools/test_native_policy.py
git diff --check
```

PR 描述应说明问题、修改后的行为、测试结果及未覆盖的实机边界。离线测试通过不能代替实际游戏验证。新增文件按 MIT 许可证贡献。

## English

This is an alpha project. Include the game Build, match/victory mode, source version, reproduction steps and a sanitized diagnostic summary. Distinguish no action, lost-target waits and normal travel/drop-off; unit counts alone do not establish healthy AI operation.

Version-specific offsets and engine calls need signature/call-path evidence. Prefer native lifecycle functions and cover manual recovery, pause, timeouts, match identity changes and foreign-AI rejection. Do not commit game binaries, proprietary AI scripts, memory dumps, personal paths or runtime logs.

Prepare the local config and run the commands above. Explain the problem, resulting behavior, test evidence and live-validation gaps in your PR. Offline tests do not replace live game validation. Contributions are provided under MIT.
