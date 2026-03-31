# 命令目录（代码对齐）

本文档是“按模块的命令索引”，用于快速定位命令入口与触发词。  
统计口径：包含已通过 `@custom_user_command` 注册并参与分发的命令；不承诺固定总数。

## 事实来源

- 注册表：`core/command/user_cmd.py`（`USER_COMMAND_CLS_DICT`）
- 实例化：`core/bot/dicebot.py`（`register_command()`）
- 模块导入链：`module/__init__.py`

## common

- `ActivateCommand`：`.bot`、`.dismiss`
- `GroupconfigCommand`：`.设置`、`.config`、`.聊天`、`.chat`、`.骰面`、`.dice`
- `ModeCommand`：`.模式`、`.mode`
- `WelcomeCommand`：`.welcome`
- `HelpCommand`：`.help`
- `NicknameCommand`：`.nn`
- `MasterCommand`：`.m`、`.master`
- `LogCommand`：`.log ...`
- `LogRecorderCommand`：日志记录器（无固定前缀，按日志状态处理）
- `LogStatCommand`：`.stat log ...`、`.stat 日志 ...`

路径：`module/common/`

## roll

- `RollDiceCommand`：`.r`
- `RollPoolCommand`：`.w`
- `RollChooseCommand`：`.c`
- `DiceSetCommand`：`.dset`
- `KarmaDiceCommand`：`.karmadice`、`.业力骰子`、`.骰子模式`、`.业力引擎`

路径：`module/roll/`

## query

- `QueryCommand`：`.q/.query/.查询`、`.s/.search/.搜索/.检索` 等
- `HomebrewCommand`：`.hb/.homebrew/.私设/.房规`

路径：`module/query/`

## deck

- `DeckCommand`：`.draw`、`.deck`
- `RandomGeneratorCommand`：`.随机`

路径：`module/deck/`

## initiative

- `InitiativeCommand`：`.init`、`.ri`、`.先攻`
- `BattlerollCommand`：`.br`、`.battleroll`、`.战斗轮`、`.round/.轮次`、`.turn/.回合`、`.skip/.跳过`、`.ed/.结束`

路径：`module/initiative/`

## character

- `CharacterDNDCommand`：`.角色卡`、`.状态`、`.生命骰`、`.长休` 及检定相关语法
- `HPCommand`：`.hp`

路径：`module/character/dnd5e/`

## misc

- `JrrpCommand`：`.jrrp`
- `UtilsDNDCommand`：`.dnd`
- `UtilsCOCCommand`：`.coc`
- `StatisticsCommand`：`.统计`
- `NewTestCommand`：JSON 邀请消息自动匹配（非点命令）

路径：`module/misc/`

## dice_hub

- `HubCommand`：`.hub`

路径：`module/dice_hub/`

## 维护建议

- 新增命令后，更新本目录而不是在多处重复记录。
- 若命令触发词有兼容分支，文档只保留“常用触发词 + 代码路径”。
- 详细参数语法应放到命令自身帮助文本或专题文档。
