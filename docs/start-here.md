# 从这里开始

DicePP 是一个面向骰主的 QQ 跑团机器人。它支持 Windows 和 Linux 部署，并提供网页管理面板，用来完成初始化、配置、运行状态查看、日志和存档管理。

如果你第一次使用，不需要先读开发文档。目标是先让机器人接入 QQ，能在群里回复 `.help`；之后再按需要启用 Persona AI、整理规则资料或调整更多配置。

安装包、规则资料查询内容和使用支持等信息，可以在交流群 `861919492` 中获取。

## 推荐路线

### Windows 部署

适合想在本机或 Windows 服务器上运行的骰主。

1. 阅读 [Windows 部署](./windows.md)，下载并启动发布包。安装包可以从发布页或交流群获取。
2. 打开网页管理面板，初始化管理员密码。
3. 配置 LLOneBot，让 QQ 机器人账号连接 DicePP。
4. 在网页管理面板中确认机器人状态，并完成账号配置。
5. 在 QQ 中向机器人发送 `.help` 验证。

### Linux 服务器部署

适合已有 Linux 服务器、希望机器人长期在线的骰主。

1. 阅读 [Linux 部署](./linux.md)，使用 Docker Compose 启动 DicePP。
2. 初始化网页管理面板管理员密码。
3. 配置 NapCat，并让它连接到 DicePP 容器。
4. 在网页管理面板中确认机器人状态，并完成账号配置。
5. 在 QQ 中向机器人发送 `.help` 验证。

## 接下来读什么

| 你想做什么 | 阅读 |
|------------|------|
| 修改账号、主人、API Key、常用开关 | [configuration.md](./configuration.md) |
| 启用 Persona AI 对话 | [persona.md](./persona.md) |
| 编写 Persona 角色卡 | [persona-character-card.md](./persona-character-card.md) |
| 查看版本发布记录 | [releases/](./releases/) |
| 参与开发或让 agent 熟悉项目 | [dev/guide.md](./dev/guide.md) |

群内具体指令请使用机器人内置 `.help` 查看。外部文档只保留部署、配置和必要维护说明，避免和机器人内置帮助重复。

## 常见判断

- 只想先跑起来：读 Windows 或 Linux 部署文档，完成 `.help` 验证即可。
- 不知道配置项写在哪里：优先使用网页管理面板；需要手动编辑时再读 [configuration.md](./configuration.md)。
- 想让机器人有 AI 角色对话能力：先确认 `.help` 正常，再读 [persona.md](./persona.md)。
- 想参与开发：直接读 [dev/guide.md](./dev/guide.md)。
