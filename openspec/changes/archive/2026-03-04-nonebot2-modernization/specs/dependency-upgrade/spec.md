## ADDED Requirements

### Requirement: NoneBot2 版本升级
项目 SHALL 使用 NoneBot2 ^2.4.0 稳定版本，替换当前的 ^2.0.0b1 beta 版本。

#### Scenario: pyproject.toml 依赖版本正确
- **WHEN** 检查 `pyproject.toml` 中的 nonebot2 依赖
- **THEN** 版本声明 SHALL 为 `^2.4.0` 或更高稳定版本

#### Scenario: requirements.txt 依赖版本正确
- **WHEN** 检查 `requirements.txt` 中的 nonebot2 依赖
- **THEN** 版本声明 SHALL 为 `>=2.4.0`

### Requirement: OneBot 适配器版本升级
项目 SHALL 使用 nonebot-adapter-onebot ^2.4.0，替换当前的 2.0.0b1 版本。

#### Scenario: 适配器依赖版本正确
- **WHEN** 检查 `pyproject.toml` 和 `requirements.txt`
- **THEN** nonebot-adapter-onebot 版本 SHALL 为 `^2.4.0` 或更高

#### Scenario: 旧适配器引用已清理
- **WHEN** 搜索项目中的 `nonebot-adapter-cqhttp` 引用
- **THEN** SHALL 找不到任何引用（已完全移除）

### Requirement: nb-cli 版本升级
项目 SHALL 使用 nb-cli ^1.4.0，替换当前的 ^0.6.4 版本。

#### Scenario: nb-cli 依赖版本正确
- **WHEN** 检查 `pyproject.toml` 中的 nb-cli 依赖
- **THEN** 版本声明 SHALL 为 `^1.4.0` 或更高

### Requirement: Python 版本要求
项目 SHALL 要求 Python >= 3.10 作为最低运行版本。

#### Scenario: pyproject.toml Python 版本要求
- **WHEN** 检查 `pyproject.toml` 中的 requires-python
- **THEN** SHALL 声明 `>=3.10`

#### Scenario: Dockerfile 使用正确 Python 版本
- **WHEN** 检查 `Dockerfile` 基础镜像
- **THEN** SHALL 使用 `python:3.12-slim` 或更高版本

### Requirement: 其他依赖版本更新
项目 SHALL 更新以下依赖到合理的现代版本。

#### Scenario: aiohttp 版本合理
- **WHEN** 检查 aiohttp 依赖版本
- **THEN** SHALL 为 `^3.9` 或更高

#### Scenario: fastapi 版本合理
- **WHEN** 检查 fastapi 依赖版本
- **THEN** SHALL 为 `>=0.100.0` 或更高

#### Scenario: uvicorn 版本合理
- **WHEN** 检查 uvicorn 依赖版本
- **THEN** SHALL 为 `>=0.24.0` 或更高

### Requirement: 依赖安装成功
升级后的依赖 SHALL 能够成功安装且无冲突。

#### Scenario: pip 安装成功
- **WHEN** 执行 `pip install -r requirements.txt`
- **THEN** SHALL 成功完成，无依赖冲突错误

#### Scenario: uv 安装成功
- **WHEN** 执行 `uv sync`
- **THEN** SHALL 成功完成，无依赖冲突错误

### Requirement: 现有测试通过
升级后 SHALL 保持所有现有测试通过。

#### Scenario: pytest 测试全部通过
- **WHEN** 执行 `pytest src/plugins/DicePP`
- **THEN** 所有测试 SHALL 通过（或仅有预期的 skip）
