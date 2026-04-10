---
name: dicepp-shell
description: "使用 DicePP Shell 工具进行交互式机器人测试. 开发时可以用来快速确认指令工作是否正常, 新功能完成前必须调用该工具进行验收."
license: MIT
metadata:
  author: DicePP
  version: "1.0"
---

## 前提条件

1. 确保在项目根目录（包含 `src/plugins/DicePP/` 的目录）
2. 确保依赖已安装：`uv pip install -e ".[dev]"`

## 使用方法

### 快速测试单个命令

```bash
# 创建会话
python -m DicePP.shell start test

# 发送命令（带确定性骰子结果）
python -m DicePP.shell send test --user player1 --msg ".r 1d20 攻击" --dice 20

# 清理
python -m DicePP.shell rm test
```

### 多步骤场景测试

```bash
# 1. 创建专门用于此场景的会话
python -m DicePP.shell start <scenario_name> --group <group_id>

# 2. 按顺序执行测试步骤
python -m DicePP.shell send <scenario_name> --user <user> --msg "<cmd>" [--dice <seq>]

# 3. 查看结果，必要时使用 --json 获取结构化输出
python -m DicePP.shell send <scenario_name> --user <user> --msg "<cmd>" --json

# 4. 完成后清理
python -m DicePP.shell rm <scenario_name>
```

### 常用命令速查

| 命令 | 用途 |
|------|------|
| `.r 1d20+5 攻击` | 掷骰 |
| `.init` | 先攻模式 |
| `.init join <name>` | 加入先攻 |
| `.init next` | 下一回合 |
| `.init end` | 结束战斗 |
| `.角色卡记录` | 创建角色卡 |
| `.状态` | 查看角色状态 |
| `.hp +/-<n>` | 修改 HP |

## 选项说明

- `--user <id>`: 用户ID（必需）
- `--msg <text>`: 消息内容（必需）
- `--dice <seq>`: 骰子序列，如 `20,18,15`（可选）
- `--json`: JSON 格式输出（可选）
- `--nick <name>`: 用户昵称（可选）
- `--private`: 私聊模式（可选）

## 示例场景

### 测试战斗流程

```bash
python -m DicePP.shell start combat
python -m DicePP.shell send combat --user DM --msg ".init"
python -m DicePP.shell send combat --user 战士 --msg ".ri" --dice 18
python -m DicePP.shell send combat --user 法师 --msg ".ri" --dice 12
python -m DicePP.shell send combat --user DM --msg ".init next"
python -m DicePP.shell rm combat
```

### 测试角色卡功能

```bash
python -m DicePP.shell start char
python -m DicePP.shell send char --user player1 --msg ".角色卡记录
$姓名$ 测试角色
$等级$ 5
$生命值$ 50/50
$生命骰$ 5/5 D10
$属性$ 16/14/13/10/12/8"
python -m DicePP.shell send char --user player1 --msg ".状态"
python -m DicePP.shell rm char
```

## 最佳实践

1. **命名会话**: 使用有意义的名称，如 `combat_test_20240110`
2. **使用 --json**: 当需要解析输出时使用 JSON 格式
3. **控制骰子**: 使用 `--dice` 确保可重复测试结果
4. **及时清理**: 测试完成后使用 `rm` 删除会话，释放磁盘空间

## 故障排除

- **会话已存在**: `start` 命令会加载已存在的会话
- **找不到会话**: 先运行 `python -m DicePP.shell start <name>`
- **输出乱码**: Windows 终端设置 `chcp 65001`
