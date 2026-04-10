# DicePP Shell - 交互式测试工具

DicePP Shell 是一个命令行工具，用于在隔离环境中测试 DicePP 机器人的各种功能。

## 获取帮助

所有命令都支持 `--help` 查看详细帮助：

```bash
# 查看主帮助
python -m DicePP.shell --help

# 查看子命令帮助
python -m DicePP.shell start --help
python -m DicePP.shell send --help
python -m DicePP.shell list --help
python -m DicePP.shell rm --help
```

## 功能特点

- **会话隔离**: 每个会话拥有独立的数据库和状态
- **骰子控制**: 可预设骰子序列，实现确定性测试
- **多格式输出**: 支持文本和 JSON 两种输出格式
- **多用户模拟**: 可模拟不同用户在同一群组中的交互

## 安装

无需额外安装，确保项目依赖已安装即可：

```bash
uv pip install -e ".[dev]"
```

## 使用方法

### 创建会话

```bash
python -m DicePP.shell start <session_name> [--group <group_id>]
```

示例：
```bash
python -m DicePP.shell start combat_test --group battle_01
```

### 发送消息

```bash
python -m DicePP.shell send <session_name> [options]
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
python -m DicePP.shell send combat_test --user player1 --msg ".r 1d20 攻击"

# 带确定性骰子结果
python -m DicePP.shell send combat_test --user player1 --msg ".r 1d20 攻击" --dice 20

# 多个骰子（多个d20用同一个序列）
python -m DicePP.shell send combat_test --user player1 --msg ".r 2d20 优势攻击" --dice 20,15

# JSON输出
python -m DicePP.shell send combat_test --user DM --msg ".init" --json
```

### 列出现有会话

```bash
python -m DicePP.shell list
```

输出示例：
```
NAME             GROUP                SIZE  LAST USED
------------------------------------------------------------
combat_test      battle_01           2.4MB   5m ago
test_session     test_group          1.1KB  1h ago
```

### 删除会话

```bash
python -m DicePP.shell rm <session_name>
```

## 典型测试场景

### 场景1：完整战斗流程

```bash
# 创建会话
python -m DicePP.shell start combat

# DM开启先攻
python -m DicePP.shell send combat --user DM --msg ".init"

# 玩家加入先攻
python -m DicePP.shell send combat --user 战士 --msg ".ri" --dice 18
python -m DicePP.shell send combat --user 法师 --msg ".ri" --dice 12

# DM添加怪物
python -m DicePP.shell send combat --user DM --msg ".ri 15 地精" --dice 15

# 查看先攻列表
python -m DicePP.shell send combat --user DM --msg ".init"

# 开始战斗
python -m DicePP.shell send combat --user DM --msg ".init next"

# 玩家攻击
python -m DicePP.shell send combat --user 战士 --msg ".r 1d20+5 攻击地精" --dice 20
python -m DicePP.shell send combat --user 战士 --msg ".r 2d6+3 伤害" --dice 6,4

# 结束战斗
python -m DicePP.shell send combat --user DM --msg ".init end"

# 清理
python -m DicePP.shell rm combat
```

### 场景2：角色卡管理

```bash
python -m DicePP.shell start char_test

# 创建角色卡
python -m DicePP.shell send char_test --user player1 --msg ".角色卡记录
$姓名$ 战士
$等级$ 5
$生命值$ 50/50
$生命骰$ 5/5 D10
$属性$ 16/14/13/10/12/8
$熟练$ 运动/威吓"

# 查看角色状态
python -m DicePP.shell send char_test --user player1 --msg ".状态"

# 修改HP
python -m DicePP.shell send char_test --user player1 --msg ".hp -10"

python -m DicePP.shell rm char_test
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
├── {session_name}/
│   ├── meta.json          # 会话元数据
│   ├── data/              # Bot 数据库
│   │   └── bot.db
│   └── logs/              # 群日志文件
│       └── {group_id}/
```

**注意**: `.dicepp-shell/` 目录已添加到 `.gitignore`，不会被提交到版本控制。

## 限制与注意事项

1. **无实际网络交互**: 所有消息仅在本地处理，不会发送到真实的 QQ
2. **单进程**: 同一时间只能运行一个 shell 命令
3. **骰子序列**: `--dice` 只影响当前消息中的骰子投掷，不影响后续消息
4. **状态隔离**: 不同会话之间数据完全隔离，但同一会话内所有用户共享状态

## 故障排除

### 编码问题

Windows 终端可能出现乱码，建议：
- 使用 Windows Terminal
- 或设置编码：`chcp 65001`

### 会话锁定

如果进程异常退出，可能需要手动删除 `.dicepp-shell/{session}/` 目录。

### 骰子序列耗尽

如果提供的骰子序列不够用，会抛出 `IndexError`，提示需要提供更多骰子值。
