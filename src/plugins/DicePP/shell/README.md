# DicePP Shell - 交互式测试工具

DicePP Shell 是一个命令行工具，用于在隔离环境中测试 DicePP 机器人的各种功能。

## 获取帮助

所有命令都支持 `--help` 查看详细帮助：

```bash
# 查看主帮助
uv run dicepp-shell --help

# 查看子命令帮助
uv run dicepp-shell init --help
uv run dicepp-shell send --help
uv run dicepp-shell serve --help
uv run dicepp-shell serve --status
uv run dicepp-shell serve --stop
uv run dicepp-shell warp --help
uv run dicepp-shell job --help
uv run dicepp-shell list --help
uv run dicepp-shell rm --help
```

## 功能特点

- **工作区隔离**: 每个会话拥有独立的配置、数据库、内容目录和 Dashboard 数据
- **骰子控制**: 可预设骰子序列，实现确定性测试
- **多格式输出**: 支持文本和 JSON 两种输出格式
- **多用户模拟**: 可模拟不同用户在同一群组中的交互
- **常驻 Runtime**: 无需 QQ 即可保持真实 Bot 生命周期并接入 Dashboard

## 安装

无需额外安装，确保项目依赖已安装即可：

```bash
uv pip install -e ".[dev]"
```

## 使用方法

### 创建会话

```bash
uv run dicepp-shell init <session_name> [--group <group_id>]
```

示例：
```bash
uv run dicepp-shell init combat_test --group battle_01
```

### 发送消息

```bash
uv run dicepp-shell send <session_name> [options]
```

**必需参数：**
- `--user <id>`: 用户ID
- `--msg <text>`: 消息内容

**可选参数：**
- `--nick <name>`: 用户昵称（默认与用户ID相同）
- `--private`: 使用私聊模式（默认是群聊）
- `--dice <seq>`: 骰子序列，如 `20,18,15,8`
- `--json`: 以JSON格式输出

示例：
```bash
# 简单掷骰
uv run dicepp-shell send combat_test --user player1 --msg ".r 1d20 攻击"

# 带确定性骰子结果
uv run dicepp-shell send combat_test --user player1 --msg ".r 1d20 攻击" --dice 20

# 多个骰子（多个d20用同一个序列）
uv run dicepp-shell send combat_test --user player1 --msg ".r 2d20 优势攻击" --dice 20,15

# JSON输出
uv run dicepp-shell send combat_test --user DM --msg ".init" --json
```

**`send` 要求会话已通过 `serve` 常驻运行**——先在另一个终端执行
`uv run dicepp-shell serve <会话名>`，再用 `send` 发消息。

### 启动常驻测试 Runtime

```bash
uv run dicepp-shell serve combat_test --port 0
```

`--port 0` 会自动选择可用端口。Runtime 仅允许监听 `127.0.0.1` 或
`::1`，不作为生产服务使用。

接入同一工作区下的本地 Dashboard：

```bash
# 终端 1：让 Dashboard 使用 init 命令打印出的 session workspace
DICEPP_PROJECT_ROOT=.dicepp-shell/combat_test uv run python -m dashboard

# 终端 2：连接该 Dashboard
uv run dicepp-shell serve combat_test \
  --dashboard http://127.0.0.1:4090
```

PowerShell 中先执行
`$env:DICEPP_PROJECT_ROOT = (Resolve-Path .dicepp-shell/combat_test)`，再启动
Dashboard。Bot 与 Dashboard 必须指向同一 session workspace，才能共享控制 token、
配置和数据库。

默认禁用自动 tick，以保证测试确定性；需要验证 Persona、scheduler 等后台
流程时显式添加 `--tick`。

### 推进 Persona 模拟时间

`warp` 是由常驻 Runtime 执行的异步任务。它不会维持一个长 HTTP 请求；CLI
提交任务后轮询状态并显示进度。`--days N` 表示从 Runtime 当前时间线连续推进
`N × 24` 小时，并在每个模拟分钟执行一次 Persona tick。运行前必须完成该
session 的 Persona、角色卡和 provider 配置，并以默认的无 tick 模式启动
`serve`；`serve --tick` Runtime 会被拒绝，避免真实后台 tick 混入模拟时间线。

```bash
# 终端 1
uv run dicepp-shell serve persona-warp

# 终端 2：先估算成本，再执行
uv run dicepp-shell warp persona-warp --days 2 --dry-run
uv run dicepp-shell warp persona-warp --days 2 --start 1351-10-26T08:00
```

第一次 warp 未指定 `--start` 时，从 Runtime 的真实启动时间开始。模拟时钟会在
warp 完成或取消后保留，后续 warp 从当前模拟时间继续；时间线已经推进后不能再次
指定 `--start`。`serve --stop` 会恢复 Runtime 启动前的时钟。

`--dry-run` 不推进时钟、不执行 tick、也不写 Persona 数据；它显示实际起止时间，
并按 DM、Character、Diary、SA 和 Proactive 的 Agent Run 上界分类估算。Agent
内部轮次仍使用正式配置，输出会同时显示 `background_llm_max_rounds` 和
`sa_max_rounds`。

完成摘要中的 proactive schedule point 只表示调度器已标记该日程点，不能据此
断言消息已经送达；发送结果需结合捕获消息、日志或 Persona trace 验收。

需要让任务脱离当前 CLI 后继续运行时：

```bash
uv run dicepp-shell warp persona-warp --days 2 --detach
uv run dicepp-shell job status persona-warp <job_id>
uv run dicepp-shell job cancel persona-warp <job_id>
```

同一 Runtime 一次只允许一个 warp。warp 期间 `send` 和普通 `serve --stop`
返回 `runtime_busy`；取消或完成后自动恢复。任务状态保存在 session 的 `jobs/`
目录中。Runtime 异常退出后，未完成任务会在下次启动时标记为 `interrupted`，
不会自动断点续跑。取消会保留已经推进到的模拟时间，方便检查或从该点继续。

查看和停止 Runtime：

```bash
uv run dicepp-shell serve --status combat_test --json
uv run dicepp-shell serve --stop combat_test
```

### 列出现有会话

```bash
uv run dicepp-shell list
```

输出示例：
```
NAME             GROUP                SIZE  LAST USED    STATE
------------------------------------------------------------------------
combat_test      battle_01           2.4MB   5m ago   running
test_session     test_group          1.1KB  1h ago   stopped
```

### 删除会话

```bash
uv run dicepp-shell rm <session_name>
```

## 典型测试场景

### 场景1：完整战斗流程

```bash
# 创建会话
uv run dicepp-shell init combat

# DM开启先攻
uv run dicepp-shell send combat --user DM --msg ".init"

# 玩家加入先攻
uv run dicepp-shell send combat --user 战士 --msg ".ri" --dice 18
uv run dicepp-shell send combat --user 法师 --msg ".ri" --dice 12

# DM添加怪物
uv run dicepp-shell send combat --user DM --msg ".ri 15 地精" --dice 15

# 查看先攻列表
uv run dicepp-shell send combat --user DM --msg ".init"

# 开始战斗
uv run dicepp-shell send combat --user DM --msg ".init next"

# 玩家攻击
uv run dicepp-shell send combat --user 战士 --msg ".r 1d20+5 攻击地精" --dice 20
uv run dicepp-shell send combat --user 战士 --msg ".r 2d6+3 伤害" --dice 6,4

# 结束战斗
uv run dicepp-shell send combat --user DM --msg ".init end"

# 清理
uv run dicepp-shell rm combat
```

### 场景2：角色卡管理

```bash
uv run dicepp-shell init char_test

# 创建角色卡
uv run dicepp-shell send char_test --user player1 --msg ".角色卡记录
$姓名$ 战士
$等级$ 5
$生命值$ 50/50
$生命骰$ 5/5 D10
$属性$ 16/14/13/10/12/8
$熟练$ 运动/威吓"

# 查看角色状态
uv run dicepp-shell send char_test --user player1 --msg ".状态"

# 修改HP
uv run dicepp-shell send char_test --user player1 --msg ".hp -10"

uv run dicepp-shell rm char_test
```

## 输出格式

### 文本格式（默认）

与在 QQ 中看到的输出一致，适合人工阅读。

### JSON 格式

使用 `--json` 参数，返回结构化数据：

```json
{
  "text": "player1 为 攻击 掷骰, 结果为 1D20=[20]=20 恭喜您!",
  "commands": [
    {
      "type": "send_msg",
      "msg": "...",
      "targets": [
        {
          "type": "GroupMessagePort",
          "group_id": "test_group"
        }
      ]
    }
  ],
  "dice_consumed": 1,
  "raw_command_count": 1
}
```

## 数据存储

会话数据存储在项目根目录的 `.dicepp-shell/` 目录下：

```
.dicepp-shell/
├── .locks/
│   └── {session_name}.lock   # 进程生命周期锁（filelock, 在 session 目录外）
├── {session_name}/
│   ├── meta.json          # 会话元数据
│   ├── config/            # 隔离配置, 不复制真实密钥
│   ├── data/              # Bot 数据库和日志
│   ├── content/           # 会话内容资源
│   ├── dashboard/data/    # Dashboard 状态
│   ├── jobs/              # warp 等后台任务状态和结果
│   └── runtime.json       # 活跃 Runtime 地址（运行时存在）
```

**注意**: `.dicepp-shell/` 目录已添加到 `.gitignore`，不会被提交到版本控制。

## 限制与注意事项

1. **无实际网络交互**: 所有消息仅在本地处理，不会发送到真实的 QQ
2. **单 Runtime**: 每个会话同一时间只能运行一个 Bot Runtime；warp 与消息处理互斥
3. **骰子序列**: `--dice` 只影响当前消息中的骰子投掷，不影响后续消息
4. **状态隔离**: 不同会话之间数据完全隔离，但同一会话内所有用户共享状态

## 故障排除

### 编码问题

Windows 终端可能出现乱码，建议：
- 使用 Windows Terminal
- 或设置编码：`chcp 65001`

### 会话锁定

Runtime 使用 OS 级文件锁（filelock），进程退出（包括崩溃）后 OS 自动释放锁；
不会出现"忘记 stop 后永久锁定"的情况。如果锁文件损坏，删除
`.dicepp-shell/.locks/<会话名>.lock` 即可。

### 骰子序列耗尽

如果提供的骰子序列不够用，会抛出 `IndexError`，提示需要提供更多骰子值。
