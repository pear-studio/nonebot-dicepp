"""
share_desire 锚点效果验收脚本（独立打分）

加载真实角色卡 + 一组覆盖各分级的事件文本，
调用 event_agent.generate_event_reaction 真实打分，
检验 LLM 是否按 prompt 锚点给出合理分布。

用法：
    uv run python scripts/dev/score_share_desire.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src" / "plugins" / "DicePP"))

from core.config.pydantic_models import PersonaConfig  # noqa: E402
from module.persona.character.loader import CharacterLoader  # noqa: E402
from module.persona.life.event_agent import EventGenerationAgent  # noqa: E402
from module.persona.llm.router import LLMRouter  # noqa: E402


# (期望区间下限, 期望区间上限, 事件描述)
SAMPLE_EVENTS: List[Tuple[float, float, str]] = [
    # 0.0~0.2 纯个人日常/重复琐事
    (0.0, 0.2, "刷牙"),
    (0.0, 0.2, "整理桌面，把笔放回笔筒"),
    (0.0, 0.2, "走神发呆了一会儿"),
    (0.0, 0.2, "去洗手间"),
    (0.0, 0.2, "叠衣服"),

    # 0.3~0.4 顺嘴可提的小事
    (0.3, 0.4, "吃了一顿普通的午饭"),
    (0.3, 0.4, "去便利店买了瓶水"),
    (0.3, 0.4, "随手翻了几页旧杂志"),
    (0.3, 0.4, "下午泡了杯茶喝"),
    (0.3, 0.4, "出门走了走，没特别去什么地方"),

    # 0.5~0.6 自然想提起
    (0.5, 0.6, "尝试做了道新菜，比想象中好吃"),
    (0.5, 0.6, "在窗外看到很漂亮的晚霞"),
    (0.5, 0.6, "突然想起小时候特别喜欢的一首歌"),
    (0.5, 0.6, "整理房间时翻到一张旧照片"),
    (0.5, 0.6, "对最近一直在追的剧产生了新的看法"),

    # 0.7~0.8 比较强的分享冲动
    (0.7, 0.8, "终于决定要去学一直想学的乐器"),
    (0.7, 0.8, "因为一件小事突然心情特别低落"),
    (0.7, 0.8, "完成了一幅自己很满意的画"),
    (0.7, 0.8, "想到一件让人忍不住吐槽的事"),
    (0.7, 0.8, "突然冒出一个很喜欢的新想法"),

    # 0.9~1.0 迫不及待
    (0.9, 1.0, "终于完成了准备很久的一件大事，激动得停不下来"),
    (0.9, 1.0, "意外收到一份很惊喜的礼物，开心到想立刻分享"),
    (0.9, 1.0, "完成了长期努力的目标，成就感强烈到必须说出去"),
    (0.9, 1.0, "突然涌起特别强烈的兴奋情绪，想立刻找人聊"),
    (0.9, 1.0, "一直纠结的难题终于想通了，整个人都轻松了"),
]


def _load_persona_config_dict() -> dict:
    """合并 global.json + secrets.json 的 persona_ai 段。"""
    cfg: dict = {}
    with open(ROOT / "config" / "global.json", encoding="utf-8") as f:
        cfg.update(json.load(f).get("persona_ai", {}))
    secrets_path = ROOT / "config" / "secrets.json"
    if secrets_path.exists():
        with open(secrets_path, encoding="utf-8") as f:
            cfg.update(json.load(f).get("persona_ai", {}))
    return cfg


async def main() -> None:
    raw = _load_persona_config_dict()
    valid_keys = set(PersonaConfig.model_fields.keys())
    filtered = {k: v for k, v in raw.items() if k in valid_keys}
    cfg = PersonaConfig(**filtered)

    if not cfg.primary_api_key:
        print("[err] 缺少 primary_api_key，请检查 config/secrets.json")
        return

    router = LLMRouter(
        primary_api_key=cfg.primary_api_key,
        primary_base_url=cfg.primary_base_url,
        primary_model=cfg.primary_model,
        auxiliary_api_key=cfg.auxiliary_api_key,
        auxiliary_base_url=cfg.auxiliary_base_url,
        auxiliary_model=cfg.auxiliary_model,
        max_concurrent=4,
        timeout=60,
        quota_check_enabled=False,
        config=cfg,
    )

    character_path_abs = ROOT / cfg.character_path
    loader = CharacterLoader(str(character_path_abs))
    character = loader.load(cfg.character_name)
    if character is None:
        print(f"[err] 角色卡加载失败: {cfg.character_name} (path={character_path_abs})")
        return

    print(f"[ok] 角色卡: {character.name}")
    print(f"[ok] 模型: {cfg.primary_model} (aux: {cfg.auxiliary_model or '(=primary)'})")
    print(f"[ok] 采样事件数: {len(SAMPLE_EVENTS)}\n")

    agent = EventGenerationAgent(router, cfg)
    sem = asyncio.Semaphore(3)

    async def call(idx: int, lo: float, hi: float, event: str):
        async with sem:
            try:
                result = await agent.generate_event_reaction(
                    event=event,
                    character_name=character.name,
                    character_description=character.description,
                    energy=70,
                    mood=70,
                    health=80,
                )
                return idx, lo, hi, event, result.share_desire, result.reaction
            except Exception as exc:  # noqa: BLE001
                return idx, lo, hi, event, None, f"ERROR: {exc}"

    tasks = [call(i, lo, hi, ev) for i, (lo, hi, ev) in enumerate(SAMPLE_EVENTS)]
    results = await asyncio.gather(*tasks)

    print(f"{'期望':<10} {'实际':<8} {'命中':<6} {'事件':<30} 反应")
    print("-" * 120)
    hit = 0
    actual_by_band: dict[str, list[float]] = {}
    rows: list[tuple] = []
    for idx, lo, hi, event, sd, reaction in sorted(results):
        band = f"{lo:.1f}~{hi:.1f}"
        if sd is None:
            print(f"{band:<10} ERR      {'-':<6} {event:<30} {reaction}")
            continue
        ok = lo <= sd <= hi
        if ok:
            hit += 1
        marker = "OK" if ok else "--"
        actual_by_band.setdefault(band, []).append(sd)
        rows.append((band, sd, marker, event, reaction))
        reaction_short = (reaction or "")[:40]
        print(f"{band:<10} {sd:<8.2f} {marker:<6} {event:<30} {reaction_short}")

    print("-" * 120)
    print(f"\n命中率: {hit}/{len(SAMPLE_EVENTS)} = {hit/len(SAMPLE_EVENTS):.0%}\n")
    print("各分级实际分布：")
    for band in sorted(actual_by_band.keys()):
        vals = actual_by_band[band]
        avg = sum(vals) / len(vals)
        print(
            f"  {band}: n={len(vals):<3} 均值 {avg:.2f}  范围 [{min(vals):.2f}, {max(vals):.2f}]"
        )


if __name__ == "__main__":
    asyncio.run(main())
