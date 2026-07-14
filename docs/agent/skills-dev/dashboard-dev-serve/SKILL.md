---
name: dashboard-dev-serve
description: 用当前 dev 源码在 5090 端口起一个 Dashboard 实例(默认仅本地 127.0.0.1,--expose 才绑 0.0.0.0),联动 dicepp-shell serve 常驻 Bot Runtime,做 Dashboard 开发联调验收。需要可视化 Dashboard 状态、调试控制通道、验证 Bot↔Dashboard 通信、测试 Dashboard 面板与真实 Bot 生命周期联动,或要在不碰生产 docker 栈的前提下用 --expose 临时把 Dashboard 暴露给外网查看时使用。
license: MIT
metadata:
  author: DicePP
  version: "1.0"
---

# Dashboard Dev Serve

用当前 dev 源码起一个测试 Dashboard(默认 `127.0.0.1:5090`,`--expose` 才绑 `0.0.0.0`),联动 `dicepp-shell serve` 常驻 Bot Runtime。全部走源码 + uv,不碰生产 docker compose,数据写入独立的 `dashboard-dev` shell workspace。

封装脚本:`docs/agent/skills-dev/dashboard-dev-serve/scripts/dev_dashboard.py`

## 何时用

- Dashboard 前端/后端开发需要真实 Bot 生命周期联动(非 mock)
- 调试控制通道(ws /ws/control)、Bot 心跳上报、Dashboard 面板状态
- 验收本 worktree 的 `dicepp-shell serve + --dashboard` 链路
- 用 `--expose` 临时把 Dashboard 暴露到局域网(5090)给远端查看,用完即停

## 端口与隔离约束(强制)

- **固定端口 5090,默认绑 `127.0.0.1`(仅本地);`--expose` 才绑 `0.0.0.0` 供局域网访问**。绝不用 `4090`——那是生产 docker dashboard 容器的宿主映射端口,会冲突。
- **固定 workspace `dashboard-dev`**(走 `.dicepp-shell/dashboard-dev/`),**不挂生产 `./config ./data ./content`**。生产 docker compose 挂的是宿主机当前目录那三个目录,本技能完全不碰。
- **Bot→Dashboard 走 `127.0.0.1:5090`**(测试 Bot 不在容器里,走宿主机栈)。生产 Bot 走容器名 `dashboard:4090`(容器间通信),两者不混用。`serve --dashboard http://127.0.0.1:5090` 会自动把 `DPP_ADMIN_HOST/PORT` 注入,Bot 启动时 `resolve_dashboard_url` 自动读到。

## 外网暴露风险声明

默认绑 `127.0.0.1`,只有本机可访问,不暴露外网。**仅当显式加 `--expose`** 时才绑 `0.0.0.0`:此时 `5090` 无额外鉴权层(Dashboard 自带登录态鉴权,但测试环境常用弱口令),**任何能连达该端口者可操作 Bot、改配置。** `--expose` 仅适合受控网络或临时验证,**用完立即 `stop`**,不要长期挂着暴露。

## 基本流程

脚本在项目根目录运行,会:
1. 预检 5090 端口(被自身以外的进程占用则报错不硬抢)
2. `dicepp-shell init dashboard-dev`(建/复用独立 workspace)
3. 后台起 Dashboard(`uv run python -m dashboard`,env: `DASHBOARD_HOST`=`127.0.0.1`(默认,`--expose` 时为 `0.0.0.0`)、`DASHBOARD_PORT=5090`、`DICEPP_PROJECT_ROOT=<workspace>`)
4. 轮询 `http://127.0.0.1:5090/api/auth/status` 直到 Dashboard ready(Dashboard 先就绪,Bot 才能立即连上控制通道)
5. 后台起 Bot Runtime(`uv run dicepp-shell serve dashboard-dev --dashboard http://127.0.0.1:5090 --tick`,默认开 `--tick` 跑真实 persona/scheduler 后台流程),等其发布 `runtime.json`
6. 打印访问地址;Dashboard 进程 PID 记到 `<workspace>/dashboard/data/.dev-pids.json`(serve 端由 dicepp-shell 自身的 lease/`runtime.json` 管理,不额外记 PID)

```bash
# 启动(默认 --tick)
python docs/agent/skills-dev/dashboard-dev-serve/scripts/dev_dashboard.py start

# 查看状态(两个进程 + 5090 是否存活)
python docs/agent/skills-dev/dashboard-dev-serve/scripts/dev_dashboard.py status

# 停止(先 stop serve 再 stop dashboard,清理 PID 文件)
python docs/agent/skills-dev/dashboard-dev-serve/scripts/dev_dashboard.py stop
```

## 选项

- `start --no-tick`:不开 `--tick`(与 dicepp-shell 默认一致),省 LLM 配额,但 Dashboard 看不到后台流程在动
- `start --dashboard-port <port>`:覆盖默认 5090(应急避让冲突时;生产仍是 4090,别用 4090)
- `start --expose`:绑 `0.0.0.0` 供局域网访问(默认仅 `127.0.0.1`)。无额外鉴权,仅限受控网络,用完即 `stop`
- `start --json`:用 JSON 打印启动结果

## 进程生命周期

脚本用 `subprocess` 后台起 Dashboard 与 serve 进程,两者脱离脚本父进程(detached)。Dashboard PID 记文件,`stop` 读它优雅退出;若 PID 文件丢失,`stop` 会扫 5090 监听端口反查。serve 端不记 PID,由 `dicepp-shell serve --stop` 经 HTTP 停止(其自身 lease 管理)。**不依赖某个 Agent 会话存活**——脚本退出后进程仍在跑,可跨多次操作。

## 不做什么

- 不 `docker compose`、不操作生产容器、不 pull 镜像
- 不写生产目录、不修改生产配置
- 不自动 commit/push
- 不调用真实 LLM 之外的付费服务(`--tick` 会驱动 persona LLM,注意配额——见下)

## --tick 与 LLM 配额提醒

默认带 `--tick` 会驱动 persona/scheduler 后台流程,**可能调用 LLM 产生费用**。首次启动前确认 `.dicepp-shell/dashboard-dev/config/` 下 Bot 的 LLM 配额与 provider 配置。省费用用 `--no-tick`,需要看后台流程动时再开。

## 典型联调场景

- **Dashboard 面板看真 Bot**:起完后浏览器开 `http://<本机 IP>:5090`,登录后能看到 Bot 注册(`shell_dashboard-dev`)、心跳、状态。发消息用 `dicepp-shell send dashboard-dev --user u1 --msg ".r 1d20"`。
- **控制通道调试**:在 Dashboard 改配置→点 reload,Bot 的 `_control_channel` 收 reload 指令并按指令断开重连。
- **验收 shell serve 链路**:本 workflow 的 e2e(`tests/e2e/test_shell_serve_runtime.py`)即为这套机制的单测版,此技能是手动联调版的封装。
