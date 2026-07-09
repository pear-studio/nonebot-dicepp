# DicePP

DicePP 是一个面向骰主的 QQ 跑团机器人，支持 Windows 和 Linux 部署。骰主可以通过网页管理面板完成初始化、配置、状态查看、运行日志和存档管理；群内玩家可以使用掷骰、规则资料查询、角色卡、先攻、日志、牌堆/随机表等跑团工具。

如果你第一次使用，建议先按 [从这里开始](docs/start-here.md) 选择部署路线。

## 适合谁

- 想给 QQ 跑团群部署机器人的骰主
- 需要稳定维护机器人配置、日志和存档的管理员
- 想在基础跑团工具之外启用 Persona AI 角色对话的用户
- 想参与开发或自行扩展功能的开发者

## 快速入口

| 你想做什么 | 阅读 |
|------------|------|
| 第一次部署 | [docs/start-here.md](docs/start-here.md) |
| Windows 部署 | [docs/windows.md](docs/windows.md) |
| Linux / Docker 部署 | [docs/linux.md](docs/linux.md) |
| 配置账号、主人、API Key、常用开关 | [docs/configuration.md](docs/configuration.md) |
| 启用 Persona AI 对话 | [docs/persona.md](docs/persona.md) |
| 参与开发 | [docs/dev/guide.md](docs/dev/guide.md) |

## 基本流程

1. 准备一个可登录的 QQ 机器人账号。
2. 启动 DicePP，并打开网页管理面板。
3. 初始化管理员密码，完成账号和常用配置。
4. 配置协议适配器（NapCat 或 LLOneBot）连接 DicePP。
5. 在 QQ 中向机器人发送 `.help`，收到回复即基本可用。

具体群内指令以机器人内置 `.help` 为准，文档不维护完整命令百科。

## 交流

交流群：`861919492`

可以在交流群中获取安装包、规则资料查询内容和使用支持等信息。
