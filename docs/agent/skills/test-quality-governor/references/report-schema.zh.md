# 报告格式

默认不要在对话中展示完整机器报告。把结构化结果写入 `.temp/test-quality/runs/<run-id>/`，只向用户展示摘要。

## state.json

```json
{
  "schema_version": 1,
  "rubric_version": 1,
  "latest_run": "2026-05-26-153000",
  "project_root": "D:/Workplace/project",
  "test_config_hash": "sha256",
  "file_hashes": {
    "tests/test_example.py": "sha256"
  }
}
```

## inventory.json

```json
{
  "schema_version": 1,
  "project_root": "D:/Workplace/project",
  "frameworks": ["pytest"],
  "test_files": [
    {
      "path": "tests/test_example.py",
      "hash": "sha256",
      "language": "python",
      "tests": [
        {
          "name": "test_user_visible_behavior",
          "line": 12,
          "markers": ["unit"],
          "fixtures": ["tmp_path"],
          "is_async": false,
          "signals": ["mock", "parametrize"]
        }
      ]
    }
  ]
}
```

## file-summary.jsonl

一行一个文件：

```json
{"type":"file-summary","file":"tests/test_example.py","total_tests":12,"quality":"mixed","avg_score":8.5,"main_issues":["弱断言","重复参数枚举"],"actions":{"keep":6,"merge":4,"rewrite":2},"risk":"medium","confidence":"high","recommended_next_step":"合并同构参数用例"}
```

`quality` 可取：

- `good`
- `mixed`
- `poor`
- `unknown`

## action-items.jsonl

一行一个测试组或动作项：

```json
{"type":"action-item","file":"tests/test_example.py","tests":["test_a","test_b"],"action":"merge","score":7,"risk":"low","confidence":"high","reason":"两个测试只差等价输入，断言同一解析规则","evidence":["duplicate-boundary"],"suggested_change":"合并为参数化测试"}
```

`risk` 可取：

- `low`
- `medium`
- `high`

`confidence` 可取：

- `high`
- `medium`
- `low`

## applied-changes.jsonl

```json
{"type":"applied-change","action":"merge","files":["tests/test_example.py"],"tests_before":4,"tests_after":1,"risk":"low","verification":"passed","notes":"合并同构参数枚举"}
```

## verification.md

记录：

- 运行了哪些命令。
- 成功/失败结果。
- 失败摘要。
- 自动修正或回退了什么。
- 剩余风险。

## summary.md

面向用户，简短说明：

- 本轮处理范围。
- 主要发现。
- 自动改动。
- 验证结果。
- 下一阶段建议。
