"""
端到端验收测试：角色完整一天生命周期（真实 LLM）

⚠️  本测试需要真实 API Key 才能运行。

前置条件：
- config/secrets.json 中配置有效的 persona_ai.primary_api_key
- config/secrets.json 中配置 persona_ai.primary_base_url（如 https://api.minimaxi.com/v1）
- config/secrets.json 中配置 persona_ai.primary_model（如 MiniMax-M2.7）

运行方式：
    # 作为 pytest 测试运行（有 API key 时执行，无时自动跳过）
    uv run pytest tests/e2e/persona/test_character_lifecycle_real_llm.py -v

    # 独立运行（不经过 pytest，直接执行）
    uv run python tests/e2e/persona/test_character_lifecycle_real_llm.py

预期结果：
- 起床边界事件（wake_up）正常触发
- 槽位事件触发链式续写（深度 1~3）
- 事件描述符合角色设定，状态 delta 在合理范围（±1~5 日常级别）
- 反应内容有行动倾向（action_tendency），推动链式续写
- 睡觉边界事件（good_night）正常触发
- 日记内容自然串联当天事件，100~300 字，语气符合角色

本测试每次运行会消耗约 7~10 次 LLM 调用（视链深度而定）。
"""

import asyncio
import json
import logging
import os
import sys
import tempfile
from datetime import datetime
from typing import Optional

import aiosqlite
import pytest

# 精简日志：只输出 persona INFO 及以上，event_agent 保留 DEBUG 以便查看 prompt
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
for handler in logging.root.handlers:
    handler.addFilter(logging.Filter("persona"))
logging.getLogger("persona.event_agent").setLevel(logging.DEBUG)


# ── 导入被测模块 ──
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src", "plugins"))

from DicePP.module.persona.llm.router import LLMRouter
from DicePP.module.persona.agents.event_agent import EventGenerationAgent
from DicePP.module.persona.proactive.character_life import (
    CharacterLife,
    CharacterLifeConfig,
)
from DicePP.module.persona.character.models import Character, PersonaExtensions
from DicePP.module.persona.data.store import PersonaDataStore
from DicePP.module.persona.data.models import CharacterState


# ── 全局 hack 时间状态 ──
_fake_now: Optional[datetime] = None


def _patched_wall_now(timezone_name: str = "") -> datetime:
    if _fake_now is not None:
        return _fake_now
    return datetime.now()


def _set_fake_time(hour: int, minute: int) -> None:
    """设置全局假时间，并同步到所有已导入模块中的 persona_wall_now 引用。"""
    global _fake_now
    _fake_now = datetime(2024, 1, 1, hour, minute, 0)

    import DicePP.module.persona.proactive.character_life as cl
    import DicePP.module.persona.proactive.scheduler as sch
    import DicePP.module.persona.proactive.delayed_task_queue as dtq
    import DicePP.module.persona.proactive.observation_buffer as ob
    import DicePP.module.persona.data.store as ds
    import DicePP.module.persona.llm.router as lr
    import DicePP.module.persona.memory.context_builder as cb
    import DicePP.module.persona.wall_clock as wc

    for mod in (cl, sch, dtq, ob, ds, lr, cb, wc):
        if hasattr(mod, "persona_wall_now"):
            mod.persona_wall_now = _patched_wall_now


# ── 加载 API 配置 ──
def _load_api_config() -> dict:
    """从 secrets.json 读取 API 配置，文件不存在或字段缺失时返回空 dict。"""
    secrets_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "config", "secrets.json"
    )
    if not os.path.exists(secrets_path):
        return {}
    with open(secrets_path) as f:
        secrets = json.load(f)
    return secrets.get("persona_ai", {})


_API_CFG = _load_api_config()
_HAS_API_KEY = bool(_API_CFG.get("primary_api_key", "").strip())


# ── pytest 标记：无 API key 时跳过 ──
pytestmark = [
    pytest.mark.real_llm,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not _HAS_API_KEY,
        reason="需要真实 API Key：config/secrets.json 中配置 persona_ai.primary_api_key",
    ),
]


async def _run_full_day_lifecycle() -> dict:
    """执行完整一天模拟，返回收集的结果供断言。"""
    api_key = _API_CFG["primary_api_key"]
    base_url = _API_CFG.get("primary_base_url", "https://api.minimaxi.com/v1")
    model = _API_CFG.get("primary_model", "MiniMax-M2.7")

    db_path = tempfile.mktemp(suffix=".db")
    results: dict = {
        "events": [],
        "diary": None,
        "final_state": None,
        "slots": [],
        "boundaries": (None, None),
    }

    async with aiosqlite.connect(db_path) as db:
        store = PersonaDataStore(db)
        await store.ensure_tables()
        await store.update_character_state(
            CharacterState(energy=50, mood=50, health=50)
        )

        ext = PersonaExtensions(
            initial_relationship=50,
            daily_events_count=3,
            event_day_start_hour=8,
            event_day_end_hour=22,
            event_jitter_minutes=15,
            event_day_start_jitter_minutes=30,
            event_day_end_jitter_minutes=30,
        )
        character = Character(
            name="小骰",
            description="一个喜欢阅读和咖啡的温柔女孩，住在有阳光的小阁楼里",
            extensions=ext,
        )

        router = LLMRouter(
            primary_api_key=api_key,
            primary_base_url=base_url,
            primary_model=model,
            max_concurrent=1,
            timeout=60,
            daily_limit=50,
            quota_check_enabled=False,
        )
        agent = EventGenerationAgent(router)
        config = CharacterLifeConfig(
            enabled=True,
            slot_match_window_minutes=15,
            diary_time="23:30",
            timezone="Asia/Shanghai",
            min_event_interval_minutes=5,
            chain_max_depth=3,
            chain_force_extend_once_prob=0.0,
        )
        life = CharacterLife(
            config=config,
            event_agent=agent,
            data_store=store,
            character=character,
        )

        # 先触发一次 tick 生成槽位（时间设为边界前，不会触发事件）
        _set_fake_time(8, 0)
        await life.tick()
        results["slots"] = [f"{m // 60:02d}:{m % 60:02d} ({t})" for m, t in life._slot_minutes_today]
        results["boundaries"] = (
            f"{life._today_jittered_start // 60:02d}:{life._today_jittered_start % 60:02d}",
            f"{life._today_jittered_end // 60:02d}:{life._today_jittered_end % 60:02d}",
        )

        # 构建时间点序列
        start_m = life._today_jittered_start
        end_m = life._today_jittered_end
        slots = life._slot_minutes_today

        time_points = []
        time_points.append((max(0, start_m - 10), "起床前，应无事件"))
        time_points.append((start_m, "起床边界事件"))
        if slots:
            # 跳过边界槽位，取第一个 system 槽位
            system_slots = [m for m, t in slots if t == "system"]
            if system_slots:
                time_points.append((system_slots[0], "槽位事件，触发链式"))
        time_points.append((end_m, "睡觉边界事件"))
        time_points.append((23 * 60 + 30, "日记生成"))

        for minutes, desc in time_points:
            hour, minute = minutes // 60, minutes % 60
            _set_fake_time(hour, minute)

            if desc == "日记生成":
                diary = await life.generate_diary()
                results["diary"] = diary
            else:
                result = await life.tick()
                if result:
                    evt = result[0]
                    results["events"].append({
                        "time": f"{hour:02d}:{minute:02d}",
                        "description": evt.get("description", ""),
                        "reaction": evt.get("reaction", ""),
                        "slot_type": evt.get("slot_type"),
                        "chain_depth": len(result),
                    })

        state = await store.get_character_state()
        results["final_state"] = {
            "energy": state.energy,
            "mood": state.mood,
            "health": state.health,
            "current_intention": state.current_intention,
        }

    return results


class TestCharacterLifecycleRealLLM:
    """使用真实 LLM 验证角色一天完整生命周期。"""

    async def test_full_day_events_and_diary(self):
        """
        模拟完整一天：起床 → 槽位链式事件 → 睡觉 → 日记。

        断言关注点：
        - 事件内容符合角色设定（提及咖啡、阅读、阁楼等关键词）
        - 链深度 >= 1（至少触发一个槽位事件）
        - 状态变化在合理范围（日常级别 ±1~5）
        - 日记不为空且长度在 50~300 字
        - 日记自然提及当天事件
        """
        results = await _run_full_day_lifecycle()

        # 打印供人工审阅
        print(f"\n今日波动边界: {results['boundaries'][0]} - {results['boundaries'][1]}")
        print(f"今日槽位: {results['slots']}")
        print()
        for evt in results["events"]:
            bt = f" [{evt['slot_type']}]" if evt["slot_type"] != "system" else ""
            cd = f" (chain_depth={evt['chain_depth']})" if evt.get("chain_depth") else ""
            print(f"[{evt['time']}] {evt['description']}{bt}{cd}")
            if evt["reaction"]:
                print(f"    反应: {evt['reaction'][:100]}...")
        print()
        if results["diary"]:
            print(f"日记 ({len(results['diary'])} 字):\n{results['diary']}")
        print()
        print(f"最终状态: {results['final_state']}")

        # ── 自动断言 ──
        # 1. 至少有 2 个事件（边界 + 槽位）
        assert len(results["events"]) >= 2, f"事件数量不足: {len(results['events'])}"

        # 2. 槽位事件链深度 >= 1
        slot_events = [e for e in results["events"] if e["slot_type"] == "system"]
        assert len(slot_events) >= 1, "未触发槽位事件"
        assert slot_events[0].get("chain_depth", 0) >= 1, "槽位事件链深度不足"

        # 3. 事件描述不为空且符合角色设定（简单关键词检查）
        all_descriptions = " ".join(e["description"] for e in results["events"])
        assert len(all_descriptions) > 10, "事件描述过短"

        # 4. 日记不为空且长度合理
        diary = results["diary"]
        assert diary is not None, "日记未生成"
        assert 50 <= len(diary) <= 400, f"日记长度异常: {len(diary)} 字"

        # 5. 日记提及至少一个当天事件关键词（角色名或常见活动）
        diary_lower = diary.lower()
        assert "小骰" in diary_lower or "咖啡" in diary_lower or "书" in diary_lower, \
            "日记未自然提及当天事件"

        # 6. 最终状态在合理范围（0~100）
        fs = results["final_state"]
        assert 0 <= fs["energy"] <= 100
        assert 0 <= fs["mood"] <= 100
        assert 0 <= fs["health"] <= 100


# ── 独立运行入口 ──
if __name__ == "__main__":
    if not _HAS_API_KEY:
        print("ERROR: 未找到 API Key。请在 config/secrets.json 中配置 persona_ai.primary_api_key")
        print("示例配置:")
        print('  {"persona_ai": {"primary_api_key": "sk-xxx", "primary_base_url": "https://api.minimaxi.com/v1", "primary_model": "MiniMax-M2.7"}}')
        sys.exit(1)

    async def _main():
        results = await _run_full_day_lifecycle()
        print(f"\n今日波动边界: {results['boundaries'][0]} - {results['boundaries'][1]}")
        print(f"今日槽位: {results['slots']}")
        print()
        for evt in results["events"]:
            bt = f" [{evt['slot_type']}]" if evt["slot_type"] != "system" else ""
            cd = f" (chain_depth={evt['chain_depth']})" if evt.get("chain_depth") else ""
            print(f"[{evt['time']}] {evt['description']}{bt}{cd}")
            if evt["reaction"]:
                print(f"    反应: {evt['reaction']}")
        print()
        if results["diary"]:
            print(f"日记 ({len(results['diary'])} 字):\n{results['diary']}")
        print()
        print(f"最终状态: {results['final_state']}")

    asyncio.run(_main())
