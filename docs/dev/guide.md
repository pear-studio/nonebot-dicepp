# 开发入口

本页是给维护者和 agent 的最小开发地图。细节以代码和测试为准，不在文档里维护代码百科。

## 代码位置

主插件代码：

```text
src/plugins/DicePP/
```

常用目录：

| 路径 | 作用 |
|------|------|
| `core/` | Bot 生命周期、命令分发、配置、本地化、数据层 |
| `module/` | 功能模块和命令实现 |
| `adapter/` | NoneBot、WebChat 等适配 |
| `module/persona/` | Persona AI |
| `module/fastapi/` | `/dpp` HTTP 接口 |
| `tests/` | 测试 |
| `docs/agent/` | agent 规则、技能和同步工具 |

## 常用搜索

查命令：

```bash
rg "custom_user_command|UserCommandBase" src/plugins/DicePP/module
```

查配置项：

```bash
rg "register_config|persona_ai|DICE_" src/plugins/DicePP config
```

查数据表和仓储：

```bash
rg "Repository|key_fields|migrations" src/plugins/DicePP/core/data src/plugins/DicePP/module
```

## 运行和测试

项目开发、测试和源码运行统一使用 Python 3.13；仓库根目录的
`.python-version` 是本地与 worktree 的解释器基线。官方 Windows/Linux 包已经
内置解释器，最终用户不需要另行安装 Python。

安装依赖（CI 使用 uv 0.11.16）：

```bash
uv sync --group dev
uv run playwright install chromium
```

固定 quick 代表集用于高频反馈，串行目标为 60 秒内：

```bash
uv run pytest -m quick -n0
```

完整离线回归（包含 Dashboard 浏览器测试，默认排除 `tests/external/`）：

```bash
uv run pytest
```

常用窄范围：

```bash
uv run pytest tests/unit/core/ -v
uv run pytest tests/integration/commands/ -v
uv run pytest tests/unit/persona/ -v
```

目录层级、资源边界、CI 分组与 external 确认规则见
[测试架构](testing.md)。

本地启动：

```bash
uv run python bot.py
```

无 QQ 的 Dashboard 和 Bot 联调直接使用当前本地入口：

```bash
uv run python -m dashboard
```

该入口在正常运行时启动一个 Bot 子进程；测试和导入 `dashboard.src.app` 不会
隐式启动 Bot。Bot 生命周期、日志尾部和停止行为由
`dashboard/src/bot_process.py` 的单一 controller 负责。

## 新增命令

1. 在对应 `module/` 子目录新增命令类。
2. 继承 `UserCommandBase`。
3. 使用 `@custom_user_command(...)` 注册。
4. 在模块 `__init__.py` 导入命令文件，确保装饰器执行。
5. 实现 `can_process_msg(...)` 和 `async process_msg(...)`。
6. 补充帮助文本，并添加必要测试。

调试命令分发时优先看：

- 命令文件是否被导入
- 优先级是否被其他命令拦截
- `can_process_msg` 是否返回处理
- 权限和群聊限制是否命中

## 数据变更

命令中通过 `self.bot.db` 使用仓储 API。

涉及 `InstanceLayout`、DataAsset、归档、空实例导入或运行时约束，先读
[Runtime 架构](runtime-architecture.md)。

涉及 schema 或持久化格式变化时：

1. 新增迁移脚本。
2. 保证迁移可重复执行。
3. 补充迁移或兼容性测试。
4. 在 release metadata 中标记数据风险。

## 发版

发版资料见 [../releases/README.md](../releases/README.md)。

延后项见 [backlog.md](./backlog.md)。

## 文档边界

用户上手文档在 `docs/` 根目录。

开发文档只维护入口和约定；命令目录、完整架构图、类级 API 说明不再手写维护。
