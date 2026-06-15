# DicePP Shell 快速参考

## 启动
```bash
uv run dicepp-shell start <session> [--group <id>]
```

## 发送消息
```bash
uv run dicepp-shell send <session> --user <id> --msg "<cmd>" [options]
```

### 常用选项
- `--dice 20,18,15` - 预设骰子序列
- `--json` - JSON 输出
- `--nick <name>` - 设置昵称
- `--private` - 私聊模式

## 管理
```bash
uv run dicepp-shell list          # 列出现有会话
uv run dicepp-shell rm <session>  # 删除会话
```

直接调用虚拟环境可执行文件时：

```powershell
# Windows
.venv\Scripts\dicepp-shell.exe start <session>

# Unix/macOS/Linux
.venv/bin/dicepp-shell start <session>
```

## 典型命令
- `.r 1d20+5 攻击` - 掷骰
- `.init` / `.init next` / `.init end` - 先攻
- `.角色卡记录` - 创建角色卡
- `.状态` - 查看角色
- `.hp +/-<n>` - 修改 HP
