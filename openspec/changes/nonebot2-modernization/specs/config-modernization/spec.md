## ADDED Requirements

### Requirement: .env.example 模板文件
项目 SHALL 提供 `.env.example` 文件作为环境变量配置模板。

#### Scenario: .env.example 文件存在
- **WHEN** 检查项目根目录
- **THEN** SHALL 存在 `.env.example` 文件

#### Scenario: .env.example 包含必要配置项
- **WHEN** 查看 `.env.example` 内容
- **THEN** SHALL 包含以下配置项及说明注释：
  - `HOST` - 监听地址
  - `PORT` - 监听端口
  - `SUPERUSERS` - 超级用户列表
  - `COMMAND_START` - 命令前缀
  - `COMMAND_SEP` - 命令分隔符

#### Scenario: .env.example 有中文注释
- **WHEN** 查看 `.env.example` 内容
- **THEN** 每个配置项 SHALL 有中文说明注释

### Requirement: pyproject.toml 现代化
`pyproject.toml` SHALL 使用现代 Python 项目配置标准。

#### Scenario: project 段落完整
- **WHEN** 检查 `pyproject.toml` 的 `[project]` 段落
- **THEN** SHALL 包含 name, version, description, requires-python, dependencies

#### Scenario: 版本号格式正确
- **WHEN** 检查 `pyproject.toml` 中的 version
- **THEN** SHALL 使用 PEP 440 兼容格式（如 `2.0.0` 而非 `1.0.0 Beta2`）

#### Scenario: NoneBot 插件配置正确
- **WHEN** 检查 `[tool.nonebot]` 或 `[nonebot.plugins]` 段落
- **THEN** SHALL 正确配置 plugin_dirs 指向 `src/plugins`

### Requirement: 依赖声明统一
项目 SHALL 确保 `pyproject.toml` 和 `requirements.txt` 的依赖版本一致。

#### Scenario: 核心依赖版本一致
- **WHEN** 比较两个文件中的 nonebot2, nonebot-adapter-onebot 版本
- **THEN** SHALL 使用兼容的版本范围

### Requirement: .gitignore 正确配置
`.gitignore` SHALL 正确忽略敏感和临时文件。

#### Scenario: .env 被忽略
- **WHEN** 检查 `.gitignore`
- **THEN** SHALL 包含 `.env` 条目（但不包含 `.env.example`）

#### Scenario: 数据目录被忽略
- **WHEN** 检查 `.gitignore`
- **THEN** SHALL 包含 `Data/Bot/` 或类似的用户数据目录

### Requirement: 移除弃用依赖引用
项目 SHALL 完全移除 `nonebot-adapter-cqhttp` 的所有引用。

#### Scenario: pyproject.toml 无旧适配器
- **WHEN** 检查 `pyproject.toml` 内容
- **THEN** SHALL 不包含 `nonebot-adapter-cqhttp` 字符串

#### Scenario: requirements.txt 无旧适配器
- **WHEN** 检查 `requirements.txt` 内容
- **THEN** SHALL 不包含 `nonebot-adapter-cqhttp` 字符串
