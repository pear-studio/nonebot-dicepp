# DicePP Common Agent Rules

## Project Context

DicePP 是基于 NoneBot2 的 QQ 骰子机器人插件，用于 TRPG 场景。

了解项目结构、模块边界和开发入口时，阅读 `docs/dev/guide.md`。

## Agent Config

Agent 规则与技能由 `docs/agent/sync.py` 管理。需要同步、检查或解释 `.codex`、`.claude`、`.kimi-code` 中的 agent 配置时，运行该脚本的 help 并按脚本输出操作。

## Working Principles

- 优先解决根因，不用临时补丁、注释逻辑、空 catch 或无意义防御性检查掩盖问题。
- 不确定时先澄清；可从代码、文档、测试中确认的事实先自行确认。
- 完成前运行与风险相称的验证；无法验证时明确说明。
- 代码审查时，可按需主动使用 `prune-pear` 补充架构复杂度审视。
- 不自动 commit 或 push，除非用户明确要求。
- 用户指令优先于普通偏好；涉及生产写入、敏感信息、付费调用或不可逆操作时，仍必须遵守环境规则中的确认要求。
