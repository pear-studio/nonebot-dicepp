"""
单元测试: PersonaDataStore — story_deck CRUD 方法

使用 SQLite :memory: 数据库，测试 upsert 5 条校验规则、
delete、get_linked_entries 等核心逻辑。
纯数据库层测试，不调 LLM。
"""
import pytest
import asyncio
from module.persona.data.store import PersonaDataStore
from module.persona.data.models import StoryDeckEntry

_TEST_DB_PATH = ":memory:"


@pytest.fixture
async def store():
    """创建使用 :memory: DB 的 PersonaDataStore"""
    import aiosqlite
    core_db = await aiosqlite.connect(":memory:")
    s = PersonaDataStore(persona_db_path=_TEST_DB_PATH, core_db=core_db)
    await s.open()
    yield s
    await s.close()
    await core_db.close()


class TestStoryDeckUpsert:
    """测试 upsert_story_deck_entry 的校验规则"""

    @pytest.mark.asyncio
    async def test_upsert_invalid_type_rejected(self, store):
        """非法 type 应被拒绝"""
        ok, err = await store.upsert_story_deck_entry("test", "invalid", "content")
        assert ok is False
        assert "无效的 type" in err

    @pytest.mark.asyncio
    async def test_upsert_key_too_short_ascii(self, store):
        """key 长度不足（纯 ASCII < 3 字符）应被拒绝"""
        ok, err = await store.upsert_story_deck_entry("ab", "entity", "content")
        assert ok is False
        assert "key 长度不足" in err

    @pytest.mark.asyncio
    async def test_upsert_key_min_length_ascii(self, store):
        """3 个 ASCII 字符 key 应通过"""
        ok, err = await store.upsert_story_deck_entry("abc", "entity", "test content")
        assert ok is True
        assert err is None

    @pytest.mark.asyncio
    async def test_upsert_key_min_length_chinese(self, store):
        """2 个汉字 key 应通过"""
        ok, err = await store.upsert_story_deck_entry("测试", "entity", "test content")
        assert ok is True
        assert err is None

    @pytest.mark.asyncio
    async def test_upsert_key_single_chinese_rejected(self, store):
        """1 个汉字 key 应被拒绝"""
        ok, err = await store.upsert_story_deck_entry("测", "entity", "content")
        assert ok is False
        assert "key 长度不足" in err

    @pytest.mark.asyncio
    async def test_upsert_content_too_long(self, store):
        """content > 300 字应被拒绝"""
        long_content = "x" * 301
        ok, err = await store.upsert_story_deck_entry("测试条目", "entity", long_content)
        assert ok is False
        assert "超长" in err

    @pytest.mark.asyncio
    async def test_upsert_content_max_length(self, store):
        """content 刚好 300 字应通过"""
        content_300 = "x" * 300
        ok, err = await store.upsert_story_deck_entry("测试条目", "entity", content_300)
        assert ok is True
        assert err is None

    @pytest.mark.asyncio
    async def test_upsert_ref_target_not_exist(self, store):
        """[[引用]] 目标不存在应被拒绝"""
        ok, err = await store.upsert_story_deck_entry(
            "测试条目", "entity", "文本引用 [[不存在的条目]] 测试"
        )
        assert ok is False
        assert "引用目标不存在" in err

    @pytest.mark.asyncio
    async def test_upsert_ref_target_exists(self, store):
        """[[引用]] 目标已存在应通过"""
        # 先创建被引用条目
        ok, _ = await store.upsert_story_deck_entry("被引用", "entity", "target content")
        assert ok is True
        # 再创建引用条目
        ok, err = await store.upsert_story_deck_entry("引用者", "entity", "引用 [[被引用]] 文本")
        assert ok is True
        assert err is None

    @pytest.mark.asyncio
    async def test_upsert_self_ref_allowed(self, store):
        """[[自引用]] 应被允许（排除在引用校验外）"""
        ok, err = await store.upsert_story_deck_entry("自引用条目", "entity", "引用 [[自引用条目]] 自己")
        assert ok is True
        assert err is None

    @pytest.mark.asyncio
    async def test_upsert_max_entries_limit(self, store):
        """超出总量上限应被拒绝"""
        # 填充到上限 (max_entries=2 for test)
        await store.upsert_story_deck_entry("条目1", "entity", "c1", max_entries=2)
        await store.upsert_story_deck_entry("条目2", "entity", "c2", max_entries=2)
        ok, err = await store.upsert_story_deck_entry("条目3", "entity", "c3", max_entries=2)
        assert ok is False
        assert "已达上限" in err

    @pytest.mark.asyncio
    async def test_upsert_update_existing(self, store):
        """update 已存在条目应通过（不检查上限）"""
        await store.upsert_story_deck_entry("条目1", "entity", "original", max_entries=1)
        ok, err = await store.upsert_story_deck_entry("条目1", "entity", "updated", max_entries=1)
        assert ok is True
        assert err is None
        entry = await store.get_story_deck_entry("条目1")
        assert entry.content == "updated"


class TestStoryDeckDelete:
    """测试 delete_story_deck_entry"""

    @pytest.mark.asyncio
    async def test_delete_existing(self, store):
        """删除已存在条目"""
        await store.upsert_story_deck_entry("测试", "entity", "content")
        ok, err, backlinks = await store.delete_story_deck_entry("测试")
        assert ok is True
        assert err is None
        assert backlinks == []

    @pytest.mark.asyncio
    async def test_delete_not_exist(self, store):
        """删除不存在条目"""
        ok, err, backlinks = await store.delete_story_deck_entry("不存在")
        assert ok is False
        assert "不存在" in err
        assert backlinks == []

    @pytest.mark.asyncio
    async def test_delete_with_backlinks(self, store):
        """删除被其他条目引用的条目应返回警告"""
        await store.upsert_story_deck_entry("目标", "entity", "target")
        await store.upsert_story_deck_entry("引用者", "entity", "引用 [[目标]] 文本")
        ok, err, backlinks = await store.delete_story_deck_entry("目标")
        assert ok is True  # 不阻止删除
        assert "引用者" in backlinks


class TestStoryDeckLinked:
    """测试 get_linked_entries"""

    @pytest.mark.asyncio
    async def test_linked_both_directions(self, store):
        """一度关联包含正向和反向引用"""
        await store.upsert_story_deck_entry("目标A", "entity", "内容")
        await store.upsert_story_deck_entry("引用B", "entity", "引用 [[目标A]] 文本")
        await store.upsert_story_deck_entry("引用C", "entity", "内容 [[引用B]] 更多")
        # 目标A 被 引用B 引用（反向），目标A 自身无引用
        linked = await store.get_linked_entries("目标A")
        keys = {e.key for e in linked}
        assert "引用B" in keys  # 引用B 引用 目标A → 反向

    @pytest.mark.asyncio
    async def test_linked_not_exist(self, store):
        """查询不存在条目的关联"""
        linked = await store.get_linked_entries("不存在")
        assert linked == []


class TestStoryDeckQuery:
    """测试 get/list/search"""

    @pytest.mark.asyncio
    async def test_search_finds_by_key(self, store):
        await store.upsert_story_deck_entry("老李", "entity", "图书管理员")
        results = await store.search_story_deck("老李")
        assert len(results) >= 1
        assert results[0].key == "老李"

    @pytest.mark.asyncio
    async def test_search_finds_by_content(self, store):
        await store.upsert_story_deck_entry("钥匙", "plot", "一把生锈的铜钥匙")
        results = await store.search_story_deck("铜钥匙")
        assert len(results) >= 1
        assert results[0].key == "钥匙"

    @pytest.mark.asyncio
    async def test_search_no_match(self, store):
        results = await store.search_story_deck("不存在的东西")
        assert results == []

    @pytest.mark.asyncio
    async def test_list_filter_by_type(self, store):
        await store.upsert_story_deck_entry("实体1", "entity", "c")
        await store.upsert_story_deck_entry("情节1", "plot", "c")
        entities = await store.list_story_deck_entries(type="entity")
        assert all(e.type == "entity" for e in entities)
        plots = await store.list_story_deck_entries(type="plot")
        assert all(e.type == "plot" for e in plots)

    @pytest.mark.asyncio
    async def test_get_count(self, store):
        assert await store.get_story_deck_count() == 0
        await store.upsert_story_deck_entry("条目1", "entity", "c")
        await store.upsert_story_deck_entry("条目2", "detail", "c")
        assert await store.get_story_deck_count() == 2
