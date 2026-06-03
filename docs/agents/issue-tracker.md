# Issue tracker: Local Markdown (Backlog)

Issues 以结构化条目存储在 `docs/dev/backlog.md`，通过 `scripts/tools/backlog.py` CLI 管理。

## 文件格式

`docs/dev/backlog.md` 包含：
- 文件头 preamble（说明性文字，以 `---` 结束）
- 按模块分组的条目（`## <module>`），每个条目格式：

```
### [B-YYMMDD-xxxxxx] 标题
- 创建: YYYY-MM-DD
- 优先级: P0/P1/P2
- 类型: bug/feature/refactor
- 改动量: S/M/L/XL
- 问题表现: ...
- 开发备忘: ...
```

## CLI 操作

```bash
# 新增
python scripts/tools/backlog.py add -m <module> -t <title> --symptom "..." --plan "..."

# 列出
python scripts/tools/backlog.py list [--module <module>]

# 查看
python scripts/tools/backlog.py show <id>

# 删除（实现完成后）
python scripts/tools/backlog.py close <id>

# 校验
python scripts/tools/backlog.py validate
```

## Agent 技能映射

| 操作 | 对应技能 |
|------|---------|
| 新增条目 | `backlog-add` |
| 核实清理 | `backlog-check` |
| 实现条目 | `backlog-implement` |

## When a skill says "publish to the issue tracker"

调用 `backlog-add` 技能或直接使用 `scripts/tools/backlog.py add`。

## When a skill says "fetch the relevant ticket"

读取 `docs/dev/backlog.md` 中对应 ID 的条目，或使用 `scripts/tools/backlog.py show <id>`。
