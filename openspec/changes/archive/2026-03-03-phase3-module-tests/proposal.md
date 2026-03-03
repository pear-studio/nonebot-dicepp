# Phase 3: 补全核心模块测试

## 背景

当前以下模块有完整逻辑但**零测试覆盖**：

| 模块 | 复杂度 | 风险 |
|------|--------|------|
| `module/roll/karma_manager.py` | 高（多种引擎、滑动窗口、方向判断） | 新功能，未经充分验证 |
| `module/common/log_command.py` + `log_db.py` | 极高（SQLite、文件导出、多 filter） | 用户最常用功能之一 |
| `module/character/coc/` | 中（能力值、HP/SAN、金钱、法术） | COC 规则计算正确性 |
| `module/common/mode_command.py` | 中（模式切换、群配置联动） | 状态变更影响全局 |
| `module/common/groupconfig_command.py` | 低-中 | 配置持久化 |

## 目标

为上述模块新增系统化的自动化测试，达到关键路径 80%+ 覆盖率。

## 非目标

- 不测试 `module/dice_hub/`（依赖外部网络）
- 不测试 `module/fastapi/`（需要 FastAPI TestClient，单独处理）
- 不追求 100% 行级覆盖率

## 成功标准

- Karma 系统：各 engine 行为可验证，KarmaConfig 序列化往返无损
- Log 系统：new/on/off/end 流程可自动化测试，SQLite 操作覆盖基本 CRUD
- COC 角色卡：能力值计算、HP/SAN 状态机的关键路径覆盖
- mode_command：模式切换前后群配置变化可断言
