# Phase 3: 技术设计

## 3A. Karma 系统测试

**文件**：`src/plugins/DicePP/module/roll/test_karma.py`

### 3A.1 KarmaConfig 序列化测试（纯单元测试）

```python
@pytest.mark.unit
@pytest.mark.karma
class TestKarmaConfig:
    def test_roundtrip_default(self):
        """默认配置序列化后反序列化，值不变。"""
        cfg = KarmaConfig()
        restored = KarmaConfig.from_dict(cfg.to_dict())
        assert cfg.enabled == restored.enabled
        assert cfg.mode == restored.mode
        assert cfg.engine == restored.engine

    def test_roundtrip_custom(self):
        """自定义配置不丢失字段。"""
        cfg = KarmaConfig()
        cfg.enabled = True
        cfg.mode = "hero"
        cfg.engine = "precise"
        cfg.custom_percentage = 70
        cfg.custom_window = 30
        restored = KarmaConfig.from_dict(cfg.to_dict())
        assert restored.mode == "hero"
        assert restored.custom_percentage == 70

    def test_from_dict_none(self):
        """from_dict(None) 返回默认配置，不抛异常。"""
        cfg = KarmaConfig.from_dict(None)
        assert cfg is not None
```

### 3A.2 KarmaState 滑动窗口测试

```python
@pytest.mark.unit
@pytest.mark.karma
class TestKarmaState:
    def test_append_and_average(self):
        state = KarmaState()
        state.resize(5)
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            state.append(v)
        assert abs(state.average() - 3.0) < 0.001

    def test_window_overflow(self):
        """超出窗口大小时，旧值被丢弃。"""
        state = KarmaState()
        state.resize(3)
        for v in [10.0, 10.0, 10.0, 1.0]:
            state.append(v)
        # 窗口只保留最新3个：10, 10, 1 → 平均 7
        assert abs(state.average() - 7.0) < 0.001

    def test_empty_average(self):
        """空状态的 average 不抛异常。"""
        state = KarmaState()
        state.resize(10)
        assert state.average() == 0.0 or state.last() is None
```

### 3A.3 各引擎行为的统计测试（蒙特卡洛）

```python
@pytest.mark.unit
@pytest.mark.karma
@pytest.mark.slow
class TestKarmaEngines:
    SAMPLES = 500  # 足够统计显著，又不过慢

    def _sample_engine(self, manager, group_id, user_id, dice_type, n) -> List[float]:
        """采样 n 次掷骰结果，返回归一化值列表。"""
        return [manager._normalize(
                    manager.generate_value(group_id, user_id, dice_type), dice_type)
                for _ in range(n)]

    def test_standard_is_uniform(self, fresh_bot):
        """standard 引擎不启用 karma 时应接近均匀分布。"""
        bot, _ = fresh_bot
        manager = get_karma_manager(bot)
        manager.disable("test_group")
        samples = self._sample_engine(manager, "test_group", "u1", 20, self.SAMPLES)
        avg = sum(samples) / len(samples)
        # 均匀分布期望值 ≈ 0.5，允许 ±0.1 误差
        assert 0.4 < avg < 0.6

    def test_hero_mode_skews_high(self, fresh_bot):
        """hero 模式下，长期平均应高于 0.5（对玩家有利）。"""
        bot, _ = fresh_bot
        manager = get_karma_manager(bot)
        manager.enable("test_group")
        manager.set_mode("test_group", "hero")
        # 预先"喂"低值历史，触发 hero 修正
        for _ in range(20):
            manager.generate_value("test_group", "u1", 20)
        samples = self._sample_engine(manager, "test_group", "u1", 20, self.SAMPLES)
        avg = sum(samples) / len(samples)
        assert avg > 0.50

    def test_grim_mode_skews_low(self, fresh_bot):
        """grim 模式下，长期平均应低于 0.5。"""
        bot, _ = fresh_bot
        manager = get_karma_manager(bot)
        manager.enable("test_group")
        manager.set_mode("test_group", "grim")
        for _ in range(20):
            manager.generate_value("test_group", "u1", 20)
        samples = self._sample_engine(manager, "test_group", "u1", 20, self.SAMPLES)
        avg = sum(samples) / len(samples)
        assert avg < 0.55  # grim 不一定强烈倾低，放宽条件
```

### 3A.4 KarmaCommand 集成测试

```python
@pytest.mark.integration
@pytest.mark.karma
class TestKarmaCommand:
    """测试 .karma 命令的启用/禁用/状态查询。"""

    async def test_enable_disable(self, fresh_bot):
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        await send_and_check(bot, ".karma on", meta, lambda s: "已启用" in s or "enable" in s.lower())
        await send_and_check(bot, ".karma off", meta, lambda s: "已禁用" in s or "disable" in s.lower())

    async def test_set_mode(self, fresh_bot):
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        await send_and_check(bot, ".karma on", meta, lambda s: True)
        await send_and_check(bot, ".karma mode hero", meta, lambda s: "hero" in s.lower() or "主角" in s)
```

---

## 3B. Log 系统测试

### 3B.1 log_db 纯单元测试（SQLite）

**文件**：`src/plugins/DicePP/module/common/test_log_db.py`

```python
@pytest.mark.unit
@pytest.mark.log
class TestLogDb:
    @pytest.fixture
    def conn(self, tmp_path, monkeypatch):
        """创建临时 SQLite 连接，patch 掉 _ensure_dir 中的路径。"""
        import sqlite3
        from module.common import log_db
        db_path = tmp_path / "test_log.db"
        conn = sqlite3.connect(str(db_path))
        log_db._init_schema(conn)
        yield conn
        conn.close()

    def test_insert_and_fetch(self, conn):
        from module.common.log_db import insert_record, fetch_records
        insert_record(conn, "log_001",
                      time="2026-01-01 00:00:00", user_id="u1",
                      nickname="Alice", content="Hello", message_id="m1",
                      source="user", is_bot=False)
        records = fetch_records(conn, "log_001")
        assert len(records) == 1
        assert records[0]["content"] == "Hello"
        assert records[0]["user_id"] == "u1"

    def test_delete_by_message_id(self, conn):
        from module.common.log_db import insert_record, fetch_records, delete_records_by_message_id
        insert_record(conn, "log_001", time="t", user_id="u1",
                      nickname="A", content="X", message_id="m1", source="user", is_bot=False)
        deleted = delete_records_by_message_id(conn, "log_001", "m1")
        assert deleted == 1
        assert len(fetch_records(conn, "log_001")) == 0

    def test_upsert_log(self, conn):
        from module.common.log_db import upsert_log, get_log_by_id
        payload = {"log_id": "log_001", "group_id": "g1", "name": "测试日志",
                   "recording": True, "logs": {}}
        upsert_log(conn, payload)
        result = get_log_by_id(conn, "log_001")
        assert result is not None
        assert result["name"] == "测试日志"
```

### 3B.2 LogCommand 集成测试

**文件**：`src/plugins/DicePP/module/common/test_log_command.py`

```python
@pytest.mark.integration
@pytest.mark.log
class TestLogCommand:
    """测试 .log 命令的完整流程。"""

    async def test_new_log(self, fresh_bot):
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        await send_and_check(bot, ".log new 冒险日志", meta,
                             lambda s: "冒险日志" in s)

    async def test_on_off_flow(self, fresh_bot):
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        await send_and_check(bot, ".log new 测试", meta, lambda s: True)
        await send_and_check(bot, ".log on 测试", meta, lambda s: "on" in s.lower() or "开启" in s)
        await send_and_check(bot, ".log off", meta, lambda s: "off" in s.lower() or "暂停" in s)
        await send_and_check(bot, ".log on 测试", meta, lambda s: True)
        await send_and_check(bot, ".log end", meta, lambda s: "end" in s.lower() or "结束" in s)

    async def test_list_logs(self, fresh_bot):
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        await send_and_check(bot, ".log new Alpha", meta, lambda s: True)
        await send_and_check(bot, ".log new Beta", meta, lambda s: True)
        await send_and_check(bot, ".log list", meta,
                             lambda s: "Alpha" in s and "Beta" in s)

    async def test_stat(self, fresh_bot):
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        await send_and_check(bot, ".log new 统计测试", meta, lambda s: True)
        await send_and_check(bot, ".log on 统计测试", meta, lambda s: True)
        # 模拟几条消息被记录
        from module.common.log_command import append_log_record
        append_log_record(bot, "g1", "u1", "Alice", "Hello!", None)
        append_log_record(bot, "g1", "u1", "Alice", ".r", None)
        await send_and_check(bot, ".log stat 统计测试", meta,
                             lambda s: "统计" in s or "stat" in s.lower())
```

---

## 3C. COC 角色卡测试

**文件**：`src/plugins/DicePP/module/character/coc/test_coc.py`

### 3C.1 能力值计算单元测试

```python
@pytest.mark.unit
class TestCocAbility:
    def test_modifier_calculation(self):
        """COC 能力值调整值计算（每10点一档）。"""
        from module.character.coc.ability import CocAbility
        ab = CocAbility()
        ab.set_value("STR", 50)
        assert ab.get_modifier("STR") == 0  # COC 不用 DnD 调整值，按规则验证
```

### 3C.2 HP/SAN 状态机测试

```python
@pytest.mark.unit
class TestCocHealth:
    def test_damage_and_recovery(self):
        from module.character.coc.health import CocHealth
        h = CocHealth()
        h.set_max_hp(10)
        h.set_current_hp(10)
        h.apply_damage(3)
        assert h.current_hp == 7
        h.apply_heal(2)
        assert h.current_hp == 9

    def test_unconscious_threshold(self):
        from module.character.coc.health import CocHealth
        h = CocHealth()
        h.set_max_hp(10)
        h.set_current_hp(10)
        h.apply_damage(10)
        assert h.current_hp == 0
        assert h.is_unconscious()

    def test_san_loss(self):
        from module.character.coc.health import CocHealth
        h = CocHealth()
        h.set_san(60)
        h.apply_san_loss(10)
        assert h.san == 50
```

### 3C.3 COC 角色卡集成测试

```python
@pytest.mark.integration
class TestCocCharCommand:
    async def test_coc_char_record_and_query(self, fresh_bot):
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        char_template = """
            $姓名$ 探索者
            $力量$ 50
            $体质$ 60
            $敏捷$ 45
            $外貌$ 55
            $智力$ 70
            $意志$ 65
            $教育$ 80
            $幸运$ 55
            $生命值$ 11
            $理智值$ 65
        """
        await send_and_check(bot, f".coc记录\n{char_template}", meta,
                             lambda s: "探索者" in s)
        await send_and_check(bot, ".coc角色卡", meta,
                             lambda s: "探索者" in s and "力量" in s)
```

---

## 3D. mode_command 测试

**文件**：`src/plugins/DicePP/module/common/test_mode_command.py`

```python
@pytest.mark.integration
class TestModeCommand:
    async def test_mode_switch_coc(self, fresh_bot):
        """切换到 COC 模式后，默认骰应该变为 D100。"""
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        await send_and_check(bot, ".mode coc", meta, lambda s: True)
        # 切换后 .r 应使用 D100
        await send_and_check(bot, ".r", meta, lambda s: "D100" in s)

    async def test_mode_switch_dnd(self, fresh_bot):
        """切换到 DnD 模式后，默认骰应该变为 D20。"""
        bot, proxy = fresh_bot
        meta = make_group_meta("g1", "u1")
        await send_and_check(bot, ".mode dnd", meta, lambda s: True)
        await send_and_check(bot, ".r", meta, lambda s: "D20" in s)
```

---

## 共用辅助函数（放在 conftest.py）

```python
from core.communication import MessageMetaData, MessageSender

def make_group_meta(group_id: str, user_id: str, 
                    nickname: str = "测试用户") -> MessageMetaData:
    return MessageMetaData("", "", MessageSender(user_id, nickname), group_id, False)

def make_private_meta(user_id: str, nickname: str = "测试用户") -> MessageMetaData:
    return MessageMetaData("", "", MessageSender(user_id, nickname), "", True)

async def send_and_check(bot, msg: str, meta: MessageMetaData, 
                          checker) -> List[BotCommandBase]:
    meta_with_msg = MessageMetaData(msg, msg, meta.sender, meta.group_id, meta.to_me)
    cmds = await bot.process_message(msg, meta_with_msg)
    result = "\n".join(str(c) for c in cmds)
    assert checker(result), f"Check failed for '{msg}': {result!r}"
    return cmds
```
