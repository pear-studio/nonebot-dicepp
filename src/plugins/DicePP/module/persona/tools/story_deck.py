"""Story Deck 工具 — SA 和 DM 共享的叙事条目图操作工具

提供 5 个工具：
- search_story_deck: DM + SA 共享，查询条目
- list_story_deck: SA 专用，分页浏览条目
- read_past_events: SA 专用，查询历史事件
- edit_story_deck: SA 专用，批量编辑条目
- edit_fronts: SA 专用，增量编辑 fronts
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional, TYPE_CHECKING
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from ..data.store import PersonaDataStore
    from ..data.models import Front, Thread

# ── Pydantic Arg Schemas ──────────────────────────────────────


class SearchStoryDeckArgs(BaseModel):
    query: str = Field(..., description="搜索关键词，匹配条目的 key 和 content")


class ListStoryDeckArgs(BaseModel):
    type: Optional[str] = Field(None, description="按类型过滤: entity | detail | plot")
    limit: Optional[int] = Field(50, description="返回条数上限（默认 50）")
    offset: Optional[int] = Field(0, description="分页偏移")


class ReadPastEventArgs(BaseModel):
    days: int = Field(..., description="查询最近 N 天的事件", ge=1, le=90)
    limit: Optional[int] = Field(20, description="返回条数上限")
    offset: Optional[int] = Field(0, description="分页偏移")


class EditStoryDeckArgs(BaseModel):
    changes: list[dict] = Field(..., description="批量编辑操作列表。每项含 action (create/update/delete) 及对应字段")


class EditFrontsArgs(BaseModel):
    changes: list[dict] = Field(..., description="增量编辑 fronts 的操作列表。每项含 action (add_thread/update_thread/remove_thread) 及对应字段")


# ── _FRONT_RULES prompt 片段 ──────────────────────────────────

_FRONT_RULES = """Front 是写给你自己的叙事规划草稿。DM 看不到它。它指导你怎么编辑条目库。

Milestone 写法：
1. 写世界给出的邀约——NPC 在做什么、环境在发生什么变化——而不是替角色做决定
2. 写具体的物件、事件、对话——而不是"关系中出现了转机"这种模糊状态
3. 事件应能和角色的身份或处境产生自然交集

Outcome 要求：
- 应具备合理的戏剧张力——无论是转折、突破、告别还是新的开始
- 需要让角色的参与能实质影响结果，不是注定的

维护节奏：
- 每天审视已有的 thread：哪条推进了？哪条偏离了？有新线出现吗？
- 偏离不是坏事——更新 thread 的方向来适配角色的真实行动
- 多条 thread 交叉时优先合并，保持焦点集中
- 不要一口气把所有 milestone 设得太满——留空间给角色的行动和 DM 的临场发挥
- 从今日事件中识别 DM 新创造的实体和线索，如有价值则条目化"""


# ── 格式化辅助 ─────────────────────────────────────────────────


def _str_field(value, default: str = "") -> str:
    """从可能是 dict 的值中安全提取字符串（防御 LLM function call 传入 dict）"""
    if isinstance(value, dict):
        val = value.get("name")
        return val.strip() if isinstance(val, str) else default
    if isinstance(value, str):
        return value.strip()
    return default


def _format_fronts(fronts: list[dict]) -> str:
    """将 fronts 列表格式化为可读文本"""
    if not fronts:
        return "（无）"
    lines = []
    for f in fronts:
        ftype = f.get("type", "unknown")
        fname = f.get("name", "未命名")
        type_label = "主线 (campaign)" if ftype == "campaign" else "支线 (adventure)"
        lines.append(f"\n【{fname}】— {type_label}")
        for t in f.get("threads", []):
            tname = t.get("name", "未命名")
            direction = t.get("direction", "")
            milestones = t.get("milestones", [])
            outcome = t.get("outcome", "")
            related = t.get("related", [])
            lines.append(f"  ▸ {tname}")
            if direction:
                lines.append(f"    走向: {direction}")
            if milestones:
                lines.append(f"    里程碑: {' → '.join(milestones)}")
            if outcome:
                lines.append(f"    终点: {outcome}")
            if related:
                lines.append(f"    关联条目: {', '.join(related)}")
    return "\n".join(lines)


def _format_story_deck_brief(entries: list) -> str:
    """将条目列表格式化为简要文本（key + type）"""
    if not entries:
        return "（无匹配条目）"
    lines = []
    for e in entries:
        key = getattr(e, "key", e.get("key", "")) if isinstance(e, dict) else e.key
        etype = getattr(e, "type", e.get("type", "")) if isinstance(e, dict) else e.type
        content = getattr(e, "content", e.get("content", "")) if isinstance(e, dict) else e.content
        lines.append(f"- {key} ({etype})")
        if content:
            preview = content[:80] + "..." if len(content) > 80 else content
            lines.append(f"  {preview}")
    return "\n".join(lines)


def _format_past_events(events: list, days: int) -> str:
    """将历史事件列表格式化为可读文本"""
    if not events:
        return f"最近 {days} 天没有事件记录。"
    lines = [f"【最近 {days} 天事件】"]
    for e in events:
        date = getattr(e, "date", e.get("date", "")) if isinstance(e, dict) else e.date
        desc = getattr(e, "description", e.get("description", "")) if isinstance(e, dict) else e.description
        reaction = getattr(e, "reaction", e.get("reaction", "")) if isinstance(e, dict) else e.reaction
        lines.append(f"[{date}] {desc}")
        if reaction:
            lines.append(f"  反应: {reaction}")
    return "\n".join(lines)


# ── 注入行格式：共享常量 + 解析函数（消除 dm_agent ↔ conversation 隐式耦合） ─

# 注入行格式: "- {key} ({type})：{content}"
_STORY_DECK_LINE_PREFIX = "- "
_STORY_DECK_TYPE_SEP = " ("


def format_injection_line(key: str, etype: str, content: str) -> str:
    """构建一条注入行。与 _parse_injection_key 互为逆向。"""
    return f"- {key} ({etype})：{content}"


def parse_injection_key(line: str) -> Optional[str]:
    """从注入行解析 key。与 format_injection_line 互为逆向。

    格式: "- {key} ({type})：..."
    返回 None 表示该行不是有效的注入行。
    """
    line = line.strip()
    if not line.startswith(_STORY_DECK_LINE_PREFIX):
        return None
    # 去掉 "- " 前缀，取 " (type)" 之前的部分作为 key
    rest = line[2:]
    key_part = rest.split(_STORY_DECK_TYPE_SEP, 1)[0].strip()
    return key_part if key_part else None



def build_search_story_deck_tool(store: "PersonaDataStore") -> "ToolSpec":
    """构建 search_story_deck 工具 (T6 新路径，替代 register_search_story_deck)"""
    from ..agent.runtime_types import (
        ToolSpec as NewToolSpec,
        ToolResult as NewToolResult,
        ToolExecutionContext,
    )

    _exec = make_search_story_deck_executor(store)

    async def _handler(args: SearchStoryDeckArgs, ctx: ToolExecutionContext) -> NewToolResult:
        try:
            kwargs = args.model_dump()
            result = await _exec(**kwargs)
            return NewToolResult(observation=result)
        except Exception as e:
            return NewToolResult(observation=f"查询失败: {e}", status="error")

    return NewToolSpec(
        name="search_story_deck",
        description=(
            "查询叙事条目库，以 query 匹配条目的名称和内容。"
            "返回匹配条目列表，精确命中时会附带直接关联的其他条目。"
        ),
        args_schema=SearchStoryDeckArgs,
        handler=_handler,
    )


# ── 工具工厂 ───────────────────────────────────────────────────


def make_search_story_deck_executor(store: "PersonaDataStore"):
    """创建 search_story_deck 执行器（DM + SA 共享）"""

    async def executor(**kwargs) -> str:
        args = SearchStoryDeckArgs(**kwargs)
        query = args.query.strip()
        if not query:
            return "请提供搜索关键词"

        entries = await store.search_story_deck(query)
        if not entries:
            return f"未找到与 '{query}' 相关的条目"

        # 对精确命中的 key，附带一度关联。优先复用 search 返回的首条结果，
        # 避免重复 get_story_deck_entry 查询
        exact_entry = entries[0] if entries[0].key == query else None
        if exact_entry is None:
            exact_entry = await store.get_story_deck_entry(query)
        linked_text = ""
        if exact_entry:
            linked = await store.get_linked_entries(query)
            if linked:
                linked_text = "\n\n关联条目:\n" + _format_story_deck_brief(linked)

        return "【Story Deck 搜索结果】\n" + _format_story_deck_brief(entries) + linked_text

    return executor


def make_list_story_deck_executor(store: "PersonaDataStore"):
    """创建 list_story_deck 执行器（SA 专用）"""

    async def executor(**kwargs) -> str:
        args = ListStoryDeckArgs(**kwargs)
        etype = args.type
        limit = max(1, min(100, args.limit or 50))
        offset = max(0, args.offset or 0)

        entries = await store.list_story_deck_entries(type=etype, limit=limit, offset=offset)
        total = await store.get_story_deck_count()

        header = f"【Story Deck 条目列表】总计 {total} 条"
        if etype:
            if etype not in ("entity", "detail", "plot"):
                header += f"（注意：type 参数无效 '{etype}'，合法值: entity/detail/plot）"
            else:
                header += f"（过滤: {etype}）"
        header += f"，当前第 {offset // limit + 1} 页"

        return header + "\n" + _format_story_deck_brief(entries)

    return executor


def make_read_past_events_executor(store: "PersonaDataStore"):
    """创建 read_past_events 执行器（SA 专用）"""

    async def executor(**kwargs) -> str:
        args = ReadPastEventArgs(**kwargs)
        days = max(1, min(90, args.days))
        limit = max(1, min(50, args.limit or 20))
        offset = max(0, args.offset or 0)

        from datetime import timedelta
        from utils.time import get_clock

        now = get_clock().now()
        start_date = (now - timedelta(days=days)).strftime("%Y-%m-%d")
        end_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")

        # 单次 SQL 范围查询替代逐日 N+1 循环
        results = await store.get_events_range(start_date, end_date)

        # 分页
        total = len(results)
        page = results[offset:offset + limit]

        return _format_past_events(page, days) + f"\n\n总计 {total} 条事件"

    return executor


def make_edit_story_deck_executor(store: "PersonaDataStore", max_entries: int = 100):
    """创建 edit_story_deck 执行器（SA 专用）"""

    async def executor(**kwargs) -> str:
        args = EditStoryDeckArgs(**kwargs)
        if not args.changes:
            return "changes 列表为空，无操作"

        applied = []
        errors = []

        for i, change in enumerate(args.changes):
            action = change.get("action", "")
            key = change.get("key", "").strip()

            if not key:
                errors.append({"index": i, "reason": "缺少 key"})
                continue

            if action == "create":
                etype = change.get("type", "").strip()
                content = change.get("content", "").strip()
                if not etype:
                    errors.append({"index": i, "key": key, "reason": "create 缺少 type"})
                    continue
                if not content:
                    errors.append({"index": i, "key": key, "reason": "create 缺少 content"})
                    continue
                # 检查 key 是否已存在
                existing = await store.get_story_deck_entry(key)
                if existing:
                    errors.append({"index": i, "key": key, "reason": f"key '{key}' 已存在，请使用 update"})
                    continue
                success, error = await store.upsert_story_deck_entry(
                    key=key, type=etype, content=content, max_entries=max_entries
                )
                if success:
                    applied.append({"action": "create", "key": key})
                else:
                    errors.append({"index": i, "key": key, "reason": error})

            elif action == "update":
                content = change.get("content", "").strip()
                if not content:
                    errors.append({"index": i, "key": key, "reason": "update 缺少 content"})
                    continue
                existing = await store.get_story_deck_entry(key)
                if not existing:
                    errors.append({"index": i, "key": key, "reason": f"key '{key}' 不存在，请使用 create"})
                    continue
                success, error = await store.upsert_story_deck_entry(
                    key=key, type=existing.type, content=content, max_entries=max_entries
                )
                if success:
                    applied.append({"action": "update", "key": key})
                else:
                    errors.append({"index": i, "key": key, "reason": error})

            elif action == "delete":
                success, error, backlinks = await store.delete_story_deck_entry(key)
                if success:
                    result = {"action": "delete", "key": key}
                    if backlinks:
                        result["warning"] = f"以下条目仍引用 [[{key}]]: {', '.join(backlinks)}"
                    applied.append(result)
                else:
                    errors.append({"index": i, "key": key, "reason": error})

            else:
                errors.append({"index": i, "key": key, "reason": f"未知 action: {action}，支持 create/update/delete"})

        result_parts = []
        if applied:
            result_parts.append(f"已应用 {len(applied)} 条: " + json.dumps(applied, ensure_ascii=False))
        if errors:
            result_parts.append(f"失败 {len(errors)} 条: " + json.dumps(errors, ensure_ascii=False))
        if not result_parts:
            return "无有效操作"
        return "\n".join(result_parts)

    return executor


def make_edit_fronts_executor(
    fronts: list,  # mutable reference to SA's fronts list
    front_max_campaign: int = 1,
    front_max_adventure: int = 2,
    threads_per_front: int = 3,
):
    """创建 edit_fronts 执行器（SA 专用）

    fronts 是对 SAState.fronts 的可变引用，工具直接修改它。
    """

    async def executor(**kwargs) -> str:
        args = EditFrontsArgs(**kwargs)
        if not args.changes:
            return "changes 列表为空，无操作"

        applied = []
        errors = []

        for i, change in enumerate(args.changes):
            action = change.get("action", "")
            front_name = _str_field(change.get("front", ""))

            if not front_name:
                errors.append({"index": i, "reason": "缺少 front 名称"})
                continue

            # 查找 front
            front = None
            for f in fronts:
                if f.get("name") == front_name:
                    front = f
                    break

            if action == "add_thread":
                thread = change.get("thread", {})
                if isinstance(thread, str):
                    # LLM 可能将 thread 传成 string，转为 dict
                    thread = {"name": thread}
                if not isinstance(thread, dict) or not thread.get("name"):
                    errors.append({"index": i, "front": front_name, "reason": "add_thread 缺少 thread.name"})
                    continue

                thread_name = _str_field(thread.get("name", ""))
                if not thread_name:
                    errors.append({"index": i, "front": front_name, "reason": "thread.name 不能为空"})
                    continue

                if front is None:
                    # 新建 front
                    ftype = _str_field(change.get("type", "adventure"), "adventure")
                    if ftype not in ("campaign", "adventure"):
                        errors.append({"index": i, "front": front_name, "reason": f"无效 type: {ftype}"})
                        continue

                    # 检查数量上限
                    campaign_count = sum(1 for f in fronts if f.get("type") == "campaign")
                    adventure_count = sum(1 for f in fronts if f.get("type") == "adventure")
                    if ftype == "campaign" and campaign_count >= front_max_campaign:
                        errors.append({"index": i, "front": front_name, "reason": f"campaign front 已达上限 {front_max_campaign}"})
                        continue
                    if ftype == "adventure" and adventure_count >= front_max_adventure:
                        errors.append({"index": i, "front": front_name, "reason": f"adventure front 已达上限 {front_max_adventure}"})
                        continue

                    new_thread = {
                        "name": thread_name,
                        "direction": thread.get("direction", ""),
                        "milestones": thread.get("milestones", [])[:4],  # 最多 4 步
                        "outcome": thread.get("outcome", ""),
                        "related": thread.get("related", []),
                    }
                    fronts.append({
                        "name": front_name,
                        "type": ftype,
                        "threads": [new_thread],
                    })
                    applied.append({"action": "add_thread", "front": front_name, "thread": thread_name, "note": "新建 front"})
                else:
                    # 已有 front，添加 thread
                    if len(front.get("threads", [])) >= threads_per_front:
                        errors.append({"index": i, "front": front_name, "reason": f"thread 已达上限 {threads_per_front}"})
                        continue

                    # 检查 thread 名是否重复
                    for t in front.get("threads", []):
                        if t.get("name") == thread_name:
                            errors.append({"index": i, "front": front_name, "reason": f"thread '{thread_name}' 已存在"})
                            break
                    else:
                        new_thread = {
                            "name": thread_name,
                            "direction": thread.get("direction", ""),
                            "milestones": thread.get("milestones", [])[:4],
                            "outcome": thread.get("outcome", ""),
                            "related": thread.get("related", []),
                        }
                        front.setdefault("threads", []).append(new_thread)
                        applied.append({"action": "add_thread", "front": front_name, "thread": thread_name})

            elif action == "update_thread":
                thread_name = _str_field(change.get("thread", ""))
                if not thread_name:
                    errors.append({"index": i, "front": front_name, "reason": "update_thread 缺少 thread 名称"})
                    continue
                if front is None:
                    errors.append({"index": i, "front": front_name, "reason": f"front '{front_name}' 不存在"})
                    continue

                target = None
                for t in front.get("threads", []):
                    if t.get("name") == thread_name:
                        target = t
                        break
                if target is None:
                    errors.append({"index": i, "front": front_name, "reason": f"thread '{thread_name}' 不存在"})
                    continue

                updates = change.get("updates", change)  # 兼容两种格式
                for field in ("direction", "milestones", "outcome", "related"):
                    if field in updates:
                        val = updates[field]
                        if field == "milestones" and isinstance(val, list):
                            val = val[:4]
                        target[field] = val
                applied.append({"action": "update_thread", "front": front_name, "thread": thread_name})

            elif action == "remove_thread":
                thread_name = _str_field(change.get("thread", ""))
                if not thread_name:
                    errors.append({"index": i, "front": front_name, "reason": "remove_thread 缺少 thread 名称"})
                    continue
                if front is None:
                    errors.append({"index": i, "front": front_name, "reason": f"front '{front_name}' 不存在"})
                    continue

                threads = front.get("threads", [])
                new_threads = [t for t in threads if t.get("name") != thread_name]
                if len(new_threads) == len(threads):
                    errors.append({"index": i, "front": front_name, "reason": f"thread '{thread_name}' 不存在"})
                else:
                    front["threads"] = new_threads
                    # 如果 front 的 threads 为空，移除整个 front
                    if not new_threads:
                        fronts[:] = [f for f in fronts if f.get("name") != front_name]
                        applied.append({"action": "remove_thread", "front": front_name, "thread": thread_name, "note": "front 已清空，已移除"})
                    else:
                        applied.append({"action": "remove_thread", "front": front_name, "thread": thread_name})

            else:
                errors.append({"index": i, "front": front_name, "reason": f"未知 action: {action}，支持 add_thread/update_thread/remove_thread"})

        result_parts = []
        if applied:
            result_parts.append(f"已应用 {len(applied)} 条: " + json.dumps(applied, ensure_ascii=False))
        if errors:
            result_parts.append(f"失败 {len(errors)} 条: " + json.dumps(errors, ensure_ascii=False))
        if not result_parts:
            return "无有效操作"
        return "\n".join(result_parts)

    return executor


# ── ToolKit 构建（新路径 T5）───────────────────────────────────


def build_sa_toolkit(
    store: "PersonaDataStore",
    fronts: list,
    max_entries: int = 100,
    front_max_campaign: int = 1,
    front_max_adventure: int = 2,
    threads_per_front: int = 3,
):
    """为 SA Agent 构建包含所有 story_deck 工具的新 ToolKit。

    T6: 返回 ToolKit（包含 ToolSpec + ToolHandler）。
    """
    from ..agent.runtime_types import ToolKit as NewToolKit
    from ..agent.runtime_types import ToolSpec as NewToolSpec
    from ..agent.runtime_types import ToolResult as NewToolResult
    from ..agent.runtime_types import ToolExecutionContext

    tools: dict[str, "NewToolSpec"] = {}

    # search_story_deck（DM + SA 共享）
    _search_exec = make_search_story_deck_executor(store)
    tools["search_story_deck"] = NewToolSpec(
        name="search_story_deck",
        description=(
            "查询叙事条目库，以 query 匹配条目的名称和内容。"
            "返回匹配条目列表，精确命中时会附带直接关联的其他条目。"
        ),
        args_schema=SearchStoryDeckArgs,
        handler=_make_sa_handler(_search_exec),
    )

    # list_story_deck
    _list_exec = make_list_story_deck_executor(store)
    tools["list_story_deck"] = NewToolSpec(
        name="list_story_deck",
        description=(
            "分页列出叙事条目库中的所有条目。可按 type 过滤（entity/detail/plot）。"
            "返回 key + type + content 列表。默认 limit=50。"
        ),
        args_schema=ListStoryDeckArgs,
        handler=_make_sa_handler(_list_exec),
    )

    # read_past_events
    _read_exec = make_read_past_events_executor(store)
    tools["read_past_events"] = NewToolSpec(
        name="read_past_events",
        description=(
            "查询最近 N 天的每日事件。返回事件列表（date + description + reaction）。"
            "按日期倒序排列。SA 用它追溯某条 thread 的发展历史。"
        ),
        args_schema=ReadPastEventArgs,
        handler=_make_sa_handler(_read_exec),
    )

    # edit_story_deck（副作用：直接写 store）
    _edit_sd_exec = make_edit_story_deck_executor(store, max_entries)
    tools["edit_story_deck"] = NewToolSpec(
        name="edit_story_deck",
        description=(
            "批量编辑叙事条目库。支持三种操作：\n"
            "create — 新增条目。必填：key（自然语言，entity 用名字，plot/detail 用名词短语 ≤15 字。"
            "最少 2 汉字）、type（entity|detail|plot）、content（条目正文，≤300 字。用 [[key]] 关联其他条目）。"
            "约束：key 已存在 → 拒绝；[[key]] 引用的目标不存在 → 拒绝；条目总数已达上限 → 拒绝。\n"
            "update — 更新条目。必填：key、content。约束：只更新 content，key 和 type 不可修改；"
            "[[key]] 引用校验同 create。\n"
            "delete — 删除条目。必填：key。约束：有其他条目 [[link]] 到此 key → 返回警告（不阻止）。\n"
            "所有 change 逐条处理，失败的不会影响成功的条目。"
            "返回 {applied: [...], errors: [{change, reason}]}。"
        ),
        args_schema=EditStoryDeckArgs,
        handler=_make_sa_handler(_edit_sd_exec),
    )

    # edit_fronts（副作用：修改 builder 闭包捕获的 fronts_dicts）
    _edit_f_exec = make_edit_fronts_executor(
        fronts, front_max_campaign, front_max_adventure, threads_per_front,
    )
    tools["edit_fronts"] = NewToolSpec(
        name="edit_fronts",
        description=(
            "增量编辑你的叙事规划前线。支持三种操作：\n"
            "add_thread — 新增叙事线。必填：front（目标 front 名称）、"
            "thread: {name, direction, milestones, outcome, related}。"
            "name — 叙事线名称；direction — 走向，一句话；milestones — 2-4 步，"
            "写世界给出的邀约，不写角色具体行动；outcome — 终点状态，需具备合理的戏剧张力；"
            "related — 关联的 story_deck 条目 key 列表。\n"
            "update_thread — 更新叙事线。必填：front、thread。可选更新字段："
            "direction / milestones / outcome / related。只传要改的字段，其余不变。\n"
            "remove_thread — 移除叙事线。必填：front、thread。用于完结或废弃的叙事线。\n"
            "限制：同时最多 1 个 campaign front + 2 个 adventure front；"
            "每个 front 最多 3 条 thread；campaign front 极少变更，不要轻易 remove 其 thread；"
            "thread 完结后评估是否从 outcome 衍生新 thread。\n"
            "逐条处理，部分成功部分失败不影响其他条目。"
        ),
        args_schema=EditFrontsArgs,
        handler=_make_sa_handler(_edit_f_exec),
    )

    return NewToolKit(tools=tools)


def _make_sa_handler(old_executor):
    """将旧式 executor(**kwargs) -> str 转为新式 handler(BaseModel, ctx) -> ToolResult"""
    from ..agent.runtime_types import ToolResult as NewToolResult

    async def handler(parsed, ctx) -> NewToolResult:
        try:
            kwargs = parsed.model_dump() if hasattr(parsed, "model_dump") else {}
            result = await old_executor(**kwargs)
            return NewToolResult(observation=result)
        except Exception as e:
            return NewToolResult(observation=f"工具执行失败: {e}", status="error")

    return handler
