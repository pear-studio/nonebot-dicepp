# 从这里开始

DicePP 是一个 TRPG 骰娘机器人，可以接入 QQ，也可以启用 Persona AI 角色对话。

如果你第一次使用，不需要先读开发文档。按你的运行环境选择一条路线即可。

## 我该看哪篇

| 你想做什么 | 阅读 |
|------------|------|
| 在 Windows 上部署 | [windows.md](./windows.md) |
| 在 Linux 服务器上用 Docker 部署 | [linux.md](./linux.md) |
| 配置账号、主人、API Key、常用开关 | [configuration.md](./configuration.md) |
| 启用 Persona AI 对话 | [persona.md](./persona.md) |
| 编写 Persona 角色卡 | [persona-character-card.md](./persona-character-card.md) |
| 参与开发或让 agent 熟悉项目 | [dev/guide.md](./dev/guide.md) |
| 查看版本发布记录 | [releases/README.md](./releases/README.md) |

## 推荐顺序

### Windows 部署

1. 按 [windows.md](./windows.md) 准备 DicePP 和 LLOneBot。
2. 按 [configuration.md](./configuration.md) 修改账号配置。
3. 如果要 AI 对话，再读 [persona.md](./persona.md)。

### Linux 服务器部署

1. 按 [linux.md](./linux.md) 部署 Docker 与 LLOneBot。
2. 按 [configuration.md](./configuration.md) 配置账号和密钥。
3. 如果要 AI 对话，再读 [persona.md](./persona.md)。

### 只想写角色

1. 先确认 DicePP 已经能运行。
2. 阅读 [persona-character-card.md](./persona-character-card.md)。
3. 写完角色卡后按 [persona.md](./persona.md) 验证加载。

## 文档维护原则

这些文档优先服务新手上手，不维护完整代码百科。

开发细节以代码、测试和 agent 搜索为准；文档只保留运行、配置、部署、Persona 使用和必要开发入口。
