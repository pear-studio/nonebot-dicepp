"""
ObservationBuffer 单元测试

测试覆盖:
- 过滤规则 (指令、emoji、长度、图片标记)
- 触发逻辑 (数量阈值、超时+最小条数)
- 动态阈值调整 (快速/慢速触发)
- 持久化 (序列化/反序列化)
"""

import pytest
from datetime import datetime, timedelta
from typing import List

from src.plugins.DicePP.module.persona.proactive.observation_buffer import (
    ObservationBuffer,
    BufferedMessage,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def buffer() -> ObservationBuffer:
    """创建基础测试缓冲区（不使用 timezone，避免时区问题）"""
    return ObservationBuffer(
        group_id="test_group_123",
        initial_threshold=10,
        max_threshold=60,
        min_threshold=5,
        max_buffer_size=60,
        max_records_per_group=30,
        timezone="",  # 使用本地时间
    )


@pytest.fixture
def buffer_with_small_threshold() -> ObservationBuffer:
    """创建小阈值的缓冲区，方便测试触发"""
    return ObservationBuffer(
        group_id="test_group_456",
        initial_threshold=3,
        max_threshold=60,
        min_threshold=5,
        max_buffer_size=60,
        max_records_per_group=30,
        timezone="",
    )


# ============================================================================
# 过滤规则测试
# ============================================================================

class TestShouldFilter:
    """测试消息过滤规则"""

    def test_filter_empty_content(self, buffer: ObservationBuffer):
        """空消息应该被过滤"""
        assert buffer.should_filter("") is True
        assert buffer.should_filter("   ") is True
        assert buffer.should_filter("\t\n") is True

    def test_filter_commands_dot(self, buffer: ObservationBuffer):
        """以 . 开头的指令应该被过滤"""
        assert buffer.should_filter(".ai 你好") is True
        assert buffer.should_filter(".roll 1d20") is True
        assert buffer.should_filter(".help") is True

    def test_filter_commands_chinese_dot(self, buffer: ObservationBuffer):
        """以 。 开头的指令应该被过滤"""
        assert buffer.should_filter("。ai 你好") is True
        assert buffer.should_filter("。roll") is True

    def test_filter_commands_slash(self, buffer: ObservationBuffer):
        """以 / 开头的指令应该被过滤"""
        assert buffer.should_filter("/cmd") is True
        assert buffer.should_filter("/start") is True

    def test_filter_commands_exclamation(self, buffer: ObservationBuffer):
        """以 ! 或 ！ 开头的指令应该被过滤"""
        assert buffer.should_filter("!help") is True
        assert buffer.should_filter("！帮助") is True

    def test_filter_pure_emoji(self, buffer: ObservationBuffer):
        """纯 emoji 消息应该被过滤"""
        assert buffer.should_filter("😀") is True
        assert buffer.should_filter("👍👍👍") is True
        assert buffer.should_filter("🎉") is True
        assert buffer.should_filter("🇨🇳") is True  # 国旗 emoji

    def test_not_filter_mixed_emoji_text(self, buffer: ObservationBuffer):
        """包含 emoji 但有文字的消息不应该被过滤"""
        assert buffer.should_filter("你好啊 😀") is False
        assert buffer.should_filter("👍 说得对呢") is False
        assert buffer.should_filter("今天天气不错🌞") is False

    def test_filter_too_short(self, buffer: ObservationBuffer):
        """少于5个字符的消息应该被过滤"""
        assert buffer.should_filter("你好") is True
        assert buffer.should_filter("1234") is True
        assert buffer.should_filter("abcde") is False  # 正好5个字符

    def test_filter_too_long(self, buffer: ObservationBuffer):
        """超过500个字符的消息应该被过滤"""
        short_msg = "a" * 500
        long_msg = "b" * 501
        assert buffer.should_filter(short_msg) is False  # 正好500字符
        assert buffer.should_filter(long_msg) is True

    def test_filter_image_marker(self, buffer: ObservationBuffer):
        """图片标记应该被过滤"""
        assert buffer.should_filter("[图片]") is True
        assert buffer.should_filter("看看这个[图片]") is True
        assert buffer.should_filter("[图片]哈哈哈") is True

    def test_filter_sticker_marker(self, buffer: ObservationBuffer):
        """表情标记应该被过滤"""
        assert buffer.should_filter("[表情]") is True
        assert buffer.should_filter("[动画表情]") is True

    def test_filter_voice_marker(self, buffer: ObservationBuffer):
        """语音标记应该被过滤"""
        assert buffer.should_filter("[语音]") is True

    def test_filter_video_marker(self, buffer: ObservationBuffer):
        """视频标记应该被过滤"""
        assert buffer.should_filter("[视频]") is True

    def test_filter_file_marker(self, buffer: ObservationBuffer):
        """文件标记应该被过滤"""
        assert buffer.should_filter("[文件]") is True

    def test_not_filter_normal_message(self, buffer: ObservationBuffer):
        """正常消息不应该被过滤"""
        assert buffer.should_filter("今天天气真不错啊") is False
        assert buffer.should_filter("有人一起打游戏吗？") is False
        assert buffer.should_filter("这是一条正常的消息内容") is False


# ============================================================================
# 触发逻辑测试
# ============================================================================

class TestTriggerLogic:
    """测试触发提取的逻辑"""

    def test_not_trigger_below_threshold(self, buffer: ObservationBuffer):
        """未达到阈值时不应该触发"""
        # 添加 9 条消息（阈值是 10）
        for i in range(9):
            triggered = buffer.add_message(f"user_{i}", f"昵称{i}", f"这是第{i}条消息内容")
            assert triggered is False

    def test_trigger_at_threshold(self, buffer_with_small_threshold: ObservationBuffer):
        """达到阈值时应该触发"""
        # 阈值是 3
        buffer_with_small_threshold.add_message("user_1", "昵称1", "第一条消息")
        buffer_with_small_threshold.add_message("user_2", "昵称2", "第二条消息")
        # 第三条应该触发
        triggered = buffer_with_small_threshold.add_message("user_3", "昵称3", "第三条消息")
        assert triggered is True

    def test_trigger_above_threshold(self, buffer_with_small_threshold: ObservationBuffer):
        """超过阈值时应该触发"""
        # 阈值是 3
        buffer_with_small_threshold.add_message("user_1", "昵称1", "第一条消息")
        buffer_with_small_threshold.add_message("user_2", "昵称2", "第二条消息")
        triggered = buffer_with_small_threshold.add_message("user_3", "昵称3", "第三条消息")
        assert triggered is True
        # 获取消息后继续添加
        buffer_with_small_threshold.get_messages_for_extraction()
        # 再添加应该还能触发
        buffer_with_small_threshold.add_message("user_4", "昵称4", "第四条消息")
        buffer_with_small_threshold.add_message("user_5", "昵称5", "第五条消息")
        triggered = buffer_with_small_threshold.add_message("user_6", "昵称6", "第六条消息")
        assert triggered is True

    def test_filtered_message_not_counted(self, buffer_with_small_threshold: ObservationBuffer):
        """被过滤的消息不应该计入触发计数"""
        # 添加 3 条会被过滤的消息
        buffer_with_small_threshold.add_message("user_1", "昵称1", ".ai 指令")
        buffer_with_small_threshold.add_message("user_2", "昵称2", "😀")
        buffer_with_small_threshold.add_message("user_3", "昵称3", "[图片]")
        # 再添加 3 条正常消息（阈值是 3）
        buffer_with_small_threshold.add_message("user_4", "昵称4", "第一条正常消息")
        buffer_with_small_threshold.add_message("user_5", "昵称5", "第二条正常消息")
        # 第三条应该触发
        triggered = buffer_with_small_threshold.add_message("user_6", "昵称6", "第三条正常消息")
        assert triggered is True

    def test_time_trigger_with_min_messages(self, buffer: ObservationBuffer):
        """2小时超时 + 至少5条消息应该触发"""
        # 手动设置旧消息时间
        old_time = datetime.now() - timedelta(hours=2, minutes=1)
        for i in range(5):
            msg = BufferedMessage(
                user_id=f"user_{i}",
                nickname=f"昵称{i}",
                content=f"旧消息{i}",
                timestamp=old_time,
            )
            buffer._buffer.append(msg)

        # 添加新消息应该触发（因为第一条消息超过2小时且缓冲>=5条）
        triggered = buffer.add_message("user_new", "新用户", "新消息触发")
        assert triggered is True

    def test_time_trigger_not_enough_messages(self, buffer: ObservationBuffer):
        """2小时超时但消息不足5条不应该触发"""
        # 手动设置旧消息时间
        old_time = datetime.now() - timedelta(hours=2, minutes=1)
        for i in range(4):  # 只有4条，不足5条
            msg = BufferedMessage(
                user_id=f"user_{i}",
                nickname=f"昵称{i}",
                content=f"旧消息{i}",
                timestamp=old_time,
            )
            buffer._buffer.append(msg)

        # 添加新消息不应该触发（虽然超时但消息不足5条）
        triggered = buffer.add_message("user_new", "新用户", "新消息")
        assert triggered is False

    def test_time_trigger_not_timeout(self, buffer: ObservationBuffer):
        """未超时不应该触发（即使消息很多）"""
        # 手动设置较新的消息时间（1小时前）
        recent_time = datetime.now() - timedelta(hours=1)
        for i in range(5):
            msg = BufferedMessage(
                user_id=f"user_{i}",
                nickname=f"昵称{i}",
                content=f"消息{i}",
                timestamp=recent_time,
            )
            buffer._buffer.append(msg)

        # 添加新消息不应该触发（未超时且未达数量阈值）
        triggered = buffer.add_message("user_new", "新用户", "新消息")
        assert triggered is False


# ============================================================================
# 动态阈值测试
# ============================================================================

class TestDynamicThreshold:
    """测试动态阈值调整"""

    def test_fast_trigger_increases_threshold(self, buffer: ObservationBuffer):
        """快速触发（<30分钟）应该增加阈值"""
        initial_threshold = buffer.threshold  # 10

        # 第一次触发
        for i in range(10):
            buffer.add_message(f"user_{i}", f"昵称{i}", f"这是第{i}条消息内容")
        assert buffer.threshold == initial_threshold  # 第一次没有上次触发时间，不调整

        messages = buffer.get_messages_for_extraction()
        assert len(messages) == 10

        # 快速再次触发（<30分钟）
        for i in range(10):
            buffer.add_message(f"user_{i}", f"昵称{i}", f"这是快速消息{i}号")

        # 阈值应该增加10
        assert buffer.threshold == initial_threshold + 10  # 20

    def test_slow_trigger_decreases_threshold(self, buffer: ObservationBuffer):
        """慢速触发（>3小时）应该减少阈值"""
        initial_threshold = buffer.threshold  # 10

        # 设置上次触发时间为3小时前
        buffer._last_trigger_time = datetime.now() - timedelta(hours=4)

        # 添加旧消息使其超时触发
        old_time = datetime.now() - timedelta(hours=2, minutes=1)
        for i in range(5):
            msg = BufferedMessage(
                user_id=f"user_{i}",
                nickname=f"昵称{i}",
                content=f"这是旧消息{i}号内容",
                timestamp=old_time,
            )
            buffer._buffer.append(msg)

        # 触发（慢速触发）
        buffer.add_message("user_new", "新用户", "这是触发消息内容")

        # 阈值应该减少5
        assert buffer.threshold == initial_threshold - 5  # 5

    def test_threshold_upper_bound(self, buffer: ObservationBuffer):
        """阈值不应该超过 max_threshold"""
        buffer.threshold = 55  # 接近上限
        buffer._last_trigger_time = datetime.now() - timedelta(minutes=10)

        # 快速触发
        buffer._adjust_threshold(fast=True)

        # 阈值应该被限制在 max_threshold (60)
        assert buffer.threshold == 60

        # 再次快速触发
        buffer._last_trigger_time = datetime.now() - timedelta(minutes=10)
        buffer._adjust_threshold(fast=True)

        # 阈值不应该超过 60
        assert buffer.threshold == 60

    def test_threshold_lower_bound(self, buffer: ObservationBuffer):
        """阈值不应该低于 min_threshold"""
        buffer.threshold = 8  # 接近下限
        buffer._last_trigger_time = datetime.now() - timedelta(hours=4)

        # 慢速触发
        buffer._adjust_threshold(fast=False)

        # 阈值应该被限制在 min_threshold (5)
        assert buffer.threshold == 5

        # 再次慢速触发
        buffer._last_trigger_time = datetime.now() - timedelta(hours=4)
        buffer._adjust_threshold(fast=False)

        # 阈值不应该低于 5
        assert buffer.threshold == 5

    def test_normal_speed_no_threshold_change(self, buffer: ObservationBuffer):
        """正常速度触发（30分钟~3小时）不应该改变阈值"""
        initial_threshold = buffer.threshold  # 10

        # 设置上次触发时间为1小时前（正常范围）
        buffer._last_trigger_time = datetime.now() - timedelta(hours=1)

        # 快速触发（但因时间间隔正常，不调整）
        buffer._adjust_threshold(fast=True)
        assert buffer.threshold == initial_threshold

        # 重置时间
        buffer._last_trigger_time = datetime.now() - timedelta(hours=2)

        # 慢速触发（但因时间间隔正常，不调整）
        buffer._adjust_threshold(fast=False)
        assert buffer.threshold == initial_threshold


# ============================================================================
# 缓冲区大小限制测试
# ============================================================================

class TestBufferSizeLimit:
    """测试缓冲区大小限制"""

    def test_buffer_size_limit(self):
        """缓冲区不应该超过 max_buffer_size"""
        buffer = ObservationBuffer(
            group_id="test_group",
            initial_threshold=100,  # 设置高阈值避免触发
            max_threshold=60,
            min_threshold=5,
            max_buffer_size=10,  # 小缓冲区方便测试
            max_records_per_group=30,
            timezone="",
        )

        # 添加 15 条消息
        for i in range(15):
            buffer.add_message(f"user_{i}", f"昵称{i}", f"这是第{i}条消息内容，足够长")

        # 缓冲区大小应该被限制在 10
        assert len(buffer._buffer) == 10

        # 应该保留最近的消息
        assert buffer._buffer[0].content == "这是第5条消息内容，足够长"
        assert buffer._buffer[-1].content == "这是第14条消息内容，足够长"


# ============================================================================
# 持久化测试
# ============================================================================

class TestPersistence:
    """测试序列化和反序列化"""

    def test_to_persist_dict_structure(self, buffer: ObservationBuffer):
        """to_persist_dict 应该返回正确的结构"""
        # 添加一些消息
        buffer.add_message("user_1", "昵称1", "第一条消息内容")
        buffer.add_message("user_2", "昵称2", "第二条消息内容")

        data = buffer.to_persist_dict()

        # 检查结构
        assert "threshold" in data
        assert "last_trigger" in data
        assert "messages" in data
        assert isinstance(data["messages"], list)
        assert len(data["messages"]) == 2

        # 检查消息结构
        msg = data["messages"][0]
        assert "user_id" in msg
        assert "nickname" in msg
        assert "content" in msg
        assert "ts" in msg

    def test_to_persist_dict_values(self, buffer: ObservationBuffer):
        """to_persist_dict 应该包含正确的值"""
        buffer.threshold = 25
        buffer._last_trigger_time = datetime(2024, 1, 15, 10, 30, 0)
        buffer.add_message("user_1", "测试昵称", "测试消息内容")

        data = buffer.to_persist_dict()

        assert data["threshold"] == 25
        assert data["last_trigger"] == "2024-01-15T10:30:00"

        msg = data["messages"][0]
        assert msg["user_id"] == "user_1"
        assert msg["nickname"] == "测试昵称"
        assert msg["content"] == "测试消息内容"

    def test_from_persist_dict_restores_state(self):
        """from_persist_dict 应该正确恢复状态"""
        # 创建原始数据
        persist_data = {
            "threshold": 35,
            "last_trigger": "2024-01-15T10:30:00",
            "messages": [
                {
                    "user_id": "user_1",
                    "nickname": "昵称1",
                    "content": "消息内容1",
                    "ts": "2024-01-15T09:00:00",
                },
                {
                    "user_id": "user_2",
                    "nickname": "昵称2",
                    "content": "消息内容2",
                    "ts": "2024-01-15T09:30:00",
                },
            ],
        }

        buffer = ObservationBuffer.from_persist_dict(
            group_id="restored_group",
            data=persist_data,
            initial_threshold=10,
            max_threshold=60,
            min_threshold=5,
            max_buffer_size=60,
            max_records_per_group=30,
            timezone="",
        )

        # 验证恢复的状态
        assert buffer.group_id == "restored_group"
        assert buffer.threshold == 35
        assert buffer._last_trigger_time == datetime(2024, 1, 15, 10, 30, 0)
        assert len(buffer._buffer) == 2

        # 验证消息内容
        msg1 = buffer._buffer[0]
        assert msg1.user_id == "user_1"
        assert msg1.nickname == "昵称1"
        assert msg1.content == "消息内容1"
        assert msg1.timestamp == datetime(2024, 1, 15, 9, 0, 0)

    def test_from_persist_dict_handles_missing_fields(self):
        """from_persist_dict 应该处理缺失字段"""
        # 缺少 last_trigger 和 messages
        persist_data = {
            "threshold": 20,
        }

        buffer = ObservationBuffer.from_persist_dict(
            group_id="test_group",
            data=persist_data,
            initial_threshold=10,
            max_threshold=60,
            min_threshold=5,
            max_buffer_size=60,
            max_records_per_group=30,
            timezone="",
        )

        assert buffer.threshold == 20
        assert buffer._last_trigger_time is None
        assert len(buffer._buffer) == 0

    def test_from_persist_dict_handles_invalid_timestamp(self):
        """from_persist_dict 应该处理无效的时间戳"""
        persist_data = {
            "threshold": 20,
            "last_trigger": "invalid-timestamp",
            "messages": [
                {
                    "user_id": "user_1",
                    "nickname": "昵称1",
                    "content": "消息内容",
                    "ts": "invalid",
                },
            ],
        }

        buffer = ObservationBuffer.from_persist_dict(
            group_id="test_group",
            data=persist_data,
            initial_threshold=10,
            max_threshold=60,
            min_threshold=5,
            max_buffer_size=60,
            max_records_per_group=30,
            timezone="",
        )

        # 无效时间戳应该被忽略
        assert buffer._last_trigger_time is None
        assert len(buffer._buffer) == 0  # 无效消息被跳过

    def test_from_persist_dict_truncates_large_buffer(self):
        """from_persist_dict 应该截断过大的缓冲区"""
        # 创建超过 max_buffer_size 的消息列表
        messages = [
            {
                "user_id": f"user_{i}",
                "nickname": f"昵称{i}",
                "content": f"消息{i}",
                "ts": "2024-01-15T09:00:00",
            }
            for i in range(15)
        ]

        persist_data = {
            "threshold": 20,
            "messages": messages,
        }

        buffer = ObservationBuffer.from_persist_dict(
            group_id="test_group",
            data=persist_data,
            initial_threshold=10,
            max_threshold=60,
            min_threshold=5,
            max_buffer_size=10,  # 小于消息数量
            max_records_per_group=30,
            timezone="",
        )

        # 应该被截断到 10 条
        assert len(buffer._buffer) == 10
        # 保留最近的消息
        assert buffer._buffer[0].content == "消息5"
        assert buffer._buffer[-1].content == "消息14"

    def test_round_trip_persistence(self, buffer: ObservationBuffer):
        """完整序列化和反序列化应该保持状态一致"""
        # 添加消息并触发一次
        for i in range(10):
            buffer.add_message(f"user_{i}", f"昵称{i}", f"消息内容{i}号，足够长")

        # 获取序列化数据
        data = buffer.to_persist_dict()

        # 恢复
        restored = ObservationBuffer.from_persist_dict(
            group_id=buffer.group_id,
            data=data,
            initial_threshold=10,
            max_threshold=60,
            min_threshold=5,
            max_buffer_size=60,
            max_records_per_group=30,
            timezone="",
        )

        # 验证状态一致
        assert restored.group_id == buffer.group_id
        assert restored.threshold == buffer.threshold
        assert restored._last_trigger_time == buffer._last_trigger_time
        assert len(restored._buffer) == len(buffer._buffer)

        # 验证消息内容一致
        for orig, rest in zip(buffer._buffer, restored._buffer):
            assert orig.user_id == rest.user_id
            assert orig.nickname == rest.nickname
            assert orig.content == rest.content
            assert orig.timestamp == rest.timestamp


# ============================================================================
# get_messages_for_extraction 测试
# ============================================================================

class TestGetMessagesForExtraction:
    """测试获取提取消息"""

    def test_returns_copy_and_clears_buffer(self, buffer: ObservationBuffer):
        """应该返回副本并清空缓冲区"""
        buffer.add_message("user_1", "昵称1", "第一条消息")
        buffer.add_message("user_2", "昵称2", "第二条消息")

        messages = buffer.get_messages_for_extraction()

        # 返回正确的消息
        assert len(messages) == 2
        assert messages[0].content == "第一条消息"
        assert messages[1].content == "第二条消息"

        # 缓冲区被清空
        assert len(buffer._buffer) == 0

    def test_returns_empty_list_when_empty(self, buffer: ObservationBuffer):
        """空缓冲区应该返回空列表"""
        messages = buffer.get_messages_for_extraction()
        assert messages == []


# ============================================================================
# get_status 测试
# ============================================================================

class TestGetStatus:
    """测试获取状态"""

    def test_status_structure(self, buffer: ObservationBuffer):
        """状态应该包含正确的字段"""
        buffer.add_message("user_1", "昵称1", "这是一条测试消息")
        buffer.threshold = 25

        status = buffer.get_status()

        assert status["buffer_size"] == 1
        assert status["threshold"] == 25
        assert status["last_trigger"] is None  # 还没有触发过

    def test_status_with_trigger(self, buffer: ObservationBuffer):
        """触发后状态应该包含上次触发时间"""
        # 强制设置上次触发时间
        trigger_time = datetime(2024, 1, 15, 10, 30, 0)
        buffer._last_trigger_time = trigger_time

        status = buffer.get_status()

        assert status["last_trigger"] == "2024-01-15T10:30:00"
