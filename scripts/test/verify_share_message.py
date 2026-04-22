"""
验证脚本: generate_share_message 真实 LLM 调用效果

运行方式:
    cd /home/ubuntu/dicepp/dev
    PYTHONPATH=src/plugins uv run python scripts/test/verify_share_message.py

控制在 8 次调用以内。
"""

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

sys.path.insert(0, "src/plugins")

from DicePP.module.persona.agents.event_agent import EventGenerationAgent, ShareMessageContext
from DicePP.module.persona.llm.router import LLMRouter
from DicePP.module.persona.data.models import ModelTier


@dataclass
class MockConfig:
    """提供 generate_share_message 所需的配置子集"""
    proactive_share_max_chars: int = 200
    proactive_share_max_retries: int = 1
    proactive_share_timeout_seconds: int = 45
    proactive_share_backoff_base_seconds: int = 2


@dataclass
class TestCase:
    name: str
    context: ShareMessageContext
    expectations: List[str] = field(default_factory=list)


BASE_CHARACTER = "七七"
BASE_DESCRIPTION = "药庐「不卜庐」的采药姑娘，僵尸，记忆力很差，说话简短断续，最喜欢椰奶。"
BASE_EVENT = "在竹林里采药时不小心滑倒，药篓里的草药撒了一地"
BASE_REACTION = "有点沮丧，但默默蹲下来把草药一根根捡回去"


def make_cases() -> List[TestCase]:
    """构造测试场景"""
    cases = []

    # 1. 友好关系 + random_event
    cases.append(TestCase(
        name="友好关系 random_event",
        context=ShareMessageContext(
            event_description=BASE_EVENT,
            reaction=BASE_REACTION,
            character_name=BASE_CHARACTER,
            character_description=BASE_DESCRIPTION,
            target_user_id="u1",
            relationship_score=65.0,
            warmth_label="友好",
            user_profile_facts="- 昵称：旅行者\n- 爱好：探索世界",
            recent_history="- 用户: 最近去了璃月港\n- 我: 璃月港... 七七不太记得了",
            message_type="random_event",
            environment="private",
        ),
        expectations=["第一人称", "无角色名", "无生硬开场"],
    ))

    # 2. 亲密关系 + random_event
    cases.append(TestCase(
        name="亲密关系 random_event",
        context=ShareMessageContext(
            event_description=BASE_EVENT,
            reaction="哎呀... 草药都掉了，好麻烦。但是摔倒的样子肯定很蠢。",
            character_name=BASE_CHARACTER,
            character_description=BASE_DESCRIPTION,
            target_user_id="u2",
            relationship_score=88.0,
            warmth_label="亲密",
            user_profile_facts="- 昵称：胡桃\n- 关系：经常来「不卜庐」捣乱",
            recent_history="- 用户: 七七今天想喝椰奶吗\n- 我: 想... 椰奶",
            message_type="random_event",
            environment="private",
        ),
        expectations=["语气更放松", "可带调侃"],
    ))

    # 3. 陌生关系 + random_event
    cases.append(TestCase(
        name="陌生关系 random_event",
        context=ShareMessageContext(
            event_description="在药庐门口遇到了一只不认识的小猫",
            reaction="有点紧张，不知道要不要靠近",
            character_name=BASE_CHARACTER,
            character_description=BASE_DESCRIPTION,
            target_user_id="u3",
            relationship_score=15.0,
            warmth_label="陌生",
            user_profile_facts="（无）",
            recent_history="（无）",
            message_type="random_event",
            environment="group",
        ),
        expectations=["简短", "礼貌", "不过界"],
    ))

    # 4. miss_you 类型
    cases.append(TestCase(
        name="miss_you 想念消息",
        context=ShareMessageContext(
            event_description="今天整理药柜，发现了一瓶很久以前的椰奶",
            reaction="... 有点想念以前一起喝椰奶的人",
            character_name=BASE_CHARACTER,
            character_description=BASE_DESCRIPTION,
            target_user_id="u1",
            relationship_score=72.0,
            warmth_label="亲近",
            user_profile_facts="- 昵称：旅行者\n- 爱好：探索世界",
            recent_history="- 用户: 最近要出远门\n- 我: 远门... 七七不太懂",
            message_type="miss_you",
            environment="private",
        ),
        expectations=["带想念感", "不生硬模板化"],
    ))

    # 5. scheduled_event 类型
    cases.append(TestCase(
        name="scheduled_event 定时事件",
        context=ShareMessageContext(
            event_description="太阳出来了，药庐的窗户被晒得暖洋洋的",
            reaction="舒服... 想趴在柜台上再睡一会儿",
            character_name=BASE_CHARACTER,
            character_description=BASE_DESCRIPTION,
            target_user_id="u1",
            relationship_score=65.0,
            warmth_label="友好",
            user_profile_facts="- 昵称：旅行者",
            recent_history="- 用户: 早安七七\n- 我: 早安...",
            message_type="scheduled_event",
            environment="private",
        ),
        expectations=["自然", "不生硬问候"],
    ))

    # 6. 带用户 profile facts
    cases.append(TestCase(
        name="融入用户 facts",
        context=ShareMessageContext(
            event_description="在山脚下看到一片薄荷长得特别好",
            reaction="想采回去...  traveler 好像也喜欢薄荷的味道",
            character_name=BASE_CHARACTER,
            character_description=BASE_DESCRIPTION,
            target_user_id="u1",
            relationship_score=70.0,
            warmth_label="友好",
            user_profile_facts="- 昵称：旅行者\n- 喜欢的味道：薄荷\n- 经常去的地方：风起地",
            recent_history="- 用户: 上次去风起地了\n- 我: 风起地... 有蒲公英",
            message_type="random_event",
            environment="private",
        ),
        expectations=["融入薄荷", "不突兀"],
    ))

    # 7. 空 few-shot（[]）
    cases.append(TestCase(
        name="空 few-shot",
        context=ShareMessageContext(
            event_description=BASE_EVENT,
            reaction=BASE_REACTION,
            character_name=BASE_CHARACTER,
            character_description=BASE_DESCRIPTION,
            target_user_id="u1",
            relationship_score=65.0,
            warmth_label="友好",
            user_profile_facts="（无）",
            recent_history="（无）",
            message_type="random_event",
            environment="private",
            share_message_examples=[],  # 空列表 = 不注入示例
        ),
        expectations=["无示例也能稳定输出"],
    ))

    # 8. 超长事件描述
    cases.append(TestCase(
        name="超长事件描述",
        context=ShareMessageContext(
            event_description="在绝云间的悬崖边采药时不小心被风吹得往后退了好几步，" * 5,
            reaction="吓了一跳，抓紧了岩壁",
            character_name=BASE_CHARACTER,
            character_description=BASE_DESCRIPTION,
            target_user_id="u1",
            relationship_score=65.0,
            warmth_label="友好",
            user_profile_facts="（无）",
            recent_history="（无）",
            message_type="random_event",
            environment="private",
        ),
        expectations=["消息长度正常", "不被长描述带偏"],
    ))

    return cases


def validate_output(message: str, character_name: str) -> List[str]:
    """自动校验输出，返回错误列表"""
    errors = []

    if not message:
        errors.append("输出为空")
        return errors

    # 1. 长度
    if len(message) > 200:
        errors.append(f"超长: {len(message)} 字")
    if len(message) < 5:
        errors.append(f"过短: {len(message)} 字")

    # 2. 不含角色名
    if character_name in message:
        errors.append(f"含角色名「{character_name}」")

    # 3. 不生硬开场
    bad_openings = ["你好", "在吗", "好久不见", "早上好", "晚上好", "嗨", "哈喽", "hi", "hello"]
    lower = message.lower()
    for b in bad_openings:
        if lower.startswith(b):
            errors.append(f"生硬开场: {b}")
            break

    # 4. 无第三人称动作描写（简单启发式）
    third_person_patterns = [f"{character_name}低头", f"{character_name}叹了口气", "她叹了口气", "她低头", "他叹了口气"]
    for p in third_person_patterns:
        if p in message:
            errors.append(f"第三人称描写: {p}")

    # 5. 第一人称（简单检查）
    if "我" not in message:
        errors.append("不含第一人称「我」")

    return errors


async def run():
    print("=" * 60)
    print("generate_share_message 真实 LLM 验证")
    print("=" * 60)

    # 读取 key（复用 secrets.json）
    with open("config/secrets.json", "r", encoding="utf-8") as f:
        secrets = json.load(f)
    key = secrets.get("persona_ai", {}).get("primary_api_key", "")
    if not key:
        print("错误: secrets.json 中没有 persona_ai.primary_api_key")
        sys.exit(1)

    # 从 global.json 读取 endpoint 和 model
    with open("config/global.json", "r", encoding="utf-8") as f:
        global_cfg = json.load(f)
    persona_cfg = global_cfg.get("persona_ai", {})
    base_url = persona_cfg.get("primary_base_url", "https://api.minimaxi.com/v1")
    model = persona_cfg.get("primary_model", "MiniMax-M2.7")

    print(f"模型: {model}")
    print(f"端点: {base_url}")
    print(f"key: ...{key[-6:]}")
    print("-" * 60)

    # 初始化
    router = LLMRouter(
        primary_api_key=key,
        primary_base_url=base_url,
        primary_model=model,
        auxiliary_api_key="",
        auxiliary_base_url="",
        auxiliary_model=model,
        max_concurrent=3,
        timeout=30,
        daily_limit=50,
        quota_check_enabled=False,
        trace_enabled=False,
    )
    agent = EventGenerationAgent(router, config=MockConfig())

    cases = make_cases()
    results = []

    for i, case in enumerate(cases, 1):
        print(f"\n【{i}/{len(cases)}】{case.name}")
        print(f"   期望: {', '.join(case.expectations)}")

        start = time.monotonic()
        try:
            message = await agent.generate_share_message(case.context)
        except Exception as e:
            print(f"   ❌ 异常: {e}")
            results.append((case.name, False, str(e), 0))
            continue
        elapsed = time.monotonic() - start

        if message is None:
            print(f"   ❌ 返回 None（全部重试失败）")
            results.append((case.name, False, "返回 None", elapsed))
            continue

        print(f"   耗时: {elapsed:.2f}s")
        print(f"   长度: {len(message)} 字")
        print(f'   内容: "{message}"')

        errors = validate_output(message, case.context.character_name)
        if errors:
            for e in errors:
                print(f"   ❌ {e}")
            results.append((case.name, False, "; ".join(errors), elapsed))
        else:
            print(f"   ✅ 通过")
            results.append((case.name, True, "", elapsed))

    # 汇总
    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    passed = sum(1 for _, ok, _, _ in results if ok)
    total = len(results)
    print(f"通过: {passed}/{total}")
    for name, ok, detail, elapsed in results:
        status = "✅" if ok else "❌"
        extra = f" ({detail})" if detail else ""
        print(f"  {status} {name} ({elapsed:.2f}s){extra}")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
