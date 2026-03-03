# nonebot-dicepp
基于Python的DND骰娘机器人, 可作为机器人项目Nonebot的插件使用

请加入交流群861919492获取整合包和部署指南

V2.0.5更新内容：
- 修复部分bug
- 优化log代码，新增外部接口

## 开发者-运行测试

### 安装测试依赖

```bash
pip install pytest pytest-asyncio pytest-cov
# 或使用 uv
uv sync --extra dev
```

### 运行测试

```bash
# 运行所有测试
pytest

# 运行测试并显示覆盖率
pytest --cov

# 运行特定目录的测试
pytest src/plugins/DicePP
```

### 测试文件命名规范

- 文件名: `unit_test.py`, `test_*.py`, `*_test.py`
- 类名: `MyTestCase`, `Test*`
- 函数名: `test*`