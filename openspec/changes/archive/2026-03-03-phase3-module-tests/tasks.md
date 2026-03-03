# Phase 3: 任务清单

## T3A. Karma 系统测试

### T3A.1 KarmaConfig 单元测试
- [x] 创建 `src/plugins/DicePP/module/roll/test_karma.py`
- [x] 实现 `TestKarmaConfig` 类
  - [x] `test_roundtrip_default`：默认配置序列化→反序列化值不变
  - [x] `test_roundtrip_custom`：自定义配置（mode/engine/window）不丢失字段
  - [x] `test_from_dict_none`：`from_dict(None)` 返回默认配置不抛异常
  - [x] `test_from_dict_partial`：字段缺失时用默认值补全

### T3A.2 KarmaState 滑动窗口测试
- [x] 实现 `TestKarmaState` 类
  - [x] `test_append_and_average`：5个值平均正确
  - [x] `test_window_overflow`：超出窗口大小时旧值被丢弃
  - [x] `test_empty_average`：空状态不抛异常
  - [x] `test_resize`：resize 后窗口大小变化正确
  - [x] `test_tail`：`tail(n)` 返回最近 n 个值

### T3A.3 引擎行为统计测试（蒙特卡洛，标记 slow）
- [x] 实现 `TestKarmaEngines` 类
  - [x] `test_standard_is_uniform`：禁用 karma 时分布接近均匀（期望 ≈ 0.5）
  - [x] `test_hero_mode_skews_high`：hero 模式长期平均 > 0.5
  - [x] `test_grim_mode_skews_low`：grim 模式长期平均 < 0.55
  - [x] `test_stable_mode_lower_variance`：stable 模式方差小于 standard

### T3A.4 KarmaCommand 集成测试
- [x] 实现 `TestKarmaCommand` 类
  - [x] `test_enable_disable`：`.karma on/off` 命令响应正确
  - [x] `test_set_mode`：`.karma mode hero` 正确切换模式
  - [x] `test_set_engine`：`.karma engine precise` 正确切换引擎
  - [x] `test_status`：`.karma status` 输出包含当前配置信息
  - [x] `test_reset_history`：`.karma reset` 清空历史

---

## T3B. Log 系统测试

### T3B.1 log_db SQLite 单元测试
- [x] 创建 `src/plugins/DicePP/module/common/test_log_db.py`
- [x] 实现带临时 SQLite 连接的 `conn` fixture
- [x] 实现 `TestLogDb` 类
  - [x] `test_insert_and_fetch`：插入记录后能正确读取
  - [x] `test_delete_by_message_id`：按消息 ID 删除记录
  - [x] `test_upsert_log`：upsert 日志元数据，幂等
  - [x] `test_get_logs_by_group`：按 group_id 查询所有日志
  - [x] `test_set_recording`：切换 recording 状态
  - [x] `test_delete_records_for_log`：删除日志的所有记录
  - [x] `test_delete_log`：删除日志元数据（级联）

### T3B.2 LogCommand 集成测试
- [x] 创建 `src/plugins/DicePP/module/common/test_log_command.py`
- [x] 实现 `TestLogCommand` 类
  - [x] `test_new_log`：`.log new 名称` 创建日志
  - [x] `test_on_off_flow`：new → on → off → on → end 完整流程
  - [ ] `test_halt`：`.log halt` 暂停不结束
  - [x] `test_list_logs`：`.log list` 列出所有日志
  - [x] `test_delete_log`：`.log delete 名称` 删除日志
  - [ ] `test_stat`：`.log stat 名称` 输出统计信息
  - [ ] `test_set_filter`：`.log set filter_bot on` 设置过滤器
  - [x] `test_append_record_via_helper`：`append_log_record()` 正确追加记录
  - [x] `test_delete_by_message_id_via_helper`：`delete_log_record_by_message_id()` 正确删除

---

## T3C. COC 角色卡测试

### T3C.1 COC 能力值单元测试
- [x] 创建 `src/plugins/DicePP/module/character/coc/test_coc.py`
- [x] 先阅读 `coc/ability.py` 确认 API，再编写测试
- [x] 实现 `TestCocAbility` 类（按实际 API 调整）

### T3C.2 COC HP/SAN 单元测试
- [x] 先阅读 `coc/health.py` 确认 HP 和 SAN 的数据结构
- [x] 实现 `TestCocHealth` 类
  - [x] `test_hp_damage_and_recovery`
  - [x] `test_hp_unconscious_threshold`（HP 降至 0 或以下）
  - [x] `test_san_loss`（SAN 减少）
  - [x] `test_san_min_zero`（SAN 不低于 0）

### T3C.3 COC 金钱系统单元测试
- [x] 先阅读 `coc/money.py` 确认 API
- [x] 实现 `TestCocMoney` 类（消费/获得/余额检查）

### T3C.4 COC 集成测试
- [x] 实现 `TestCocCharCommand` 类
  - [x] `test_char_record_and_query`：记录角色卡后可查询
  - [x] `test_skill_check`：技能检定命令（`.侦察` 等）
  - [ ] `test_san_check`：`.san` 命令（成功/失败 SAN 损失）

---

## T3D. mode_command 测试

### T3D.1 模式切换测试
- [x] 创建 `src/plugins/DicePP/module/common/test_mode_command.py`
- [x] 先阅读 `mode_command.py` 确认可用模式名称
- [x] 实现 `TestModeCommand` 类
  - [x] `test_mode_switch_to_coc`：切换到 COC 后 `.r` 使用 D100
  - [x] `test_mode_switch_to_dnd`：切换到 DnD 后 `.r` 使用 D20
  - [x] `test_mode_invalid`：非法模式名报错
  - [ ] `test_mode_persistence`：切换后数据持久化（重新初始化 bot 后模式保持）

---

## T3E. 公共辅助函数
- [x] 在 `src/plugins/DicePP/conftest.py` 中添加 `make_group_meta()`
- [x] 在 `conftest.py` 中添加 `make_private_meta()`
- [x] 在 `conftest.py` 中添加 `send_and_check()` 异步辅助函数
