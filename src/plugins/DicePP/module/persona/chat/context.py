"""
上下文构建器

组装四层记忆到 LLM 消息列表
"""
from dataclasses import dataclass
from nonebot.log import logger
from typing import List, Dict, Optional, Any, Tuple

from utils.string import estimate_tokens

from ..character.models import Character
from ..data.models import UserProfile
from ..wall_clock import persona_wall_now, format_timestamp, format_relative_time

DEFAULT_DELAY_BEFORE = 1.0


@dataclass
class SegmentGuide:
    """分段回复引导参数，None 表示不注入分段引导。"""

    enabled: bool
    target_chars: int
    max_chars: int
    soft_limit: int
    hard_limit: int


class ContextBuilder:
    """上下文构建器"""

    def __init__(
        self,
        character: Character,
        max_history_turns: int = 10,
        max_history_tokens: int = 4000,
        timezone: str = "Asia/Shanghai",
        lore_token_budget: int = 300,
        segment_guide: Optional[SegmentGuide] = None,
    ):
        self.character = character
        self.max_history_turns = max_history_turns
        self.max_history_tokens = max_history_tokens
        self.timezone = timezone
        self.lore_token_budget = lore_token_budget
        self.segment_guide = segment_guide

    def update_character(self, character: Character) -> None:
        """同步新的角色卡引用"""
        self.character = character

    def build(
        self,
        formatted_history: List[Dict[str, str]],
        history_dicts: List[Dict[str, str]],
        user_profile: Optional[UserProfile] = None,
        diary_context: str = "",
        warmth_label: str = "友好",
    ) -> List[Dict[str, str]]:
        messages = []

        # 世界书扫描仍用 raw dicts（未格式化的 content 避免时间戳/speaker 前缀干扰匹配）
        lore_sections = self.build_lore_text(history_dicts)

        # 合并所有 system 内容为一条（某些提供商如 MiniMax 不支持多条 system 消息）
        system_parts = []

        system_prompt = self._build_system_prompt(user_profile, diary_context, warmth_label, lore_sections)
        system_parts.append(system_prompt)

        if self.character.mes_example:
            example = self.character.format_mes_example()
            system_parts.append(f"示例对话:\n{example}")

        messages.append({"role": "system", "content": "\n\n".join(system_parts)})

        # 追加格式化后的历史消息对（末尾即为当前用户消息，带时间戳前缀）
        for msg in formatted_history:
            messages.append({"role": msg["role"], "content": msg["content"]})

        return messages

    def build_lore_text(
        self,
        history_dicts: List[Dict[str, str]],
    ) -> Dict[str, List[str]]:
        """扫描文本并返回按位置分类的世界书内容

        history_dicts 的 content 字段应为原始内容（无格式化前缀），
        世界书关键词扫描依赖纯净文本。末尾条目即为当前用户消息。
        扫描是顺序无关的（集合语义，命中关键词即止）。

        返回结构为 {"before_char": [...], "after_char": [...]}，
        即使目前 LoreEntry 没有 position 字段，也为后续扩展留接口。
        默认所有条目归入 "after_char"（与当前硬编码位置一致）。
        """
        sections: Dict[str, List[str]] = {"before_char": [], "after_char": []}
        if not self.character or not self.character.character_book:
            return sections

        texts_to_scan = []
        for msg in history_dicts:
            texts_to_scan.append(msg.get("content", ""))

        matched = self.character.search_lore_entries(texts_to_scan)

        if not matched:
            return sections

        # 按优先级降序排列，数值越高越优先注入
        matched.sort(key=lambda e: e.order, reverse=True)

        # Token 预算控制（基于字符统计的估算值，不引入真实 tokenizer）
        budget = self.lore_token_budget
        total_tokens = 0.0
        selected = []
        for entry in matched:
            cost = estimate_tokens(entry.content)
            if total_tokens + cost > budget:
                break
            total_tokens += cost
            selected.append(entry)

        if not selected:
            return sections

        # 收集命中的 keys 用于日志（取第一条命中的 key 作为代表）
        scanned = "\n".join(texts_to_scan)
        hit_keys = []
        for e in selected:
            for k in e.keys:
                if k in scanned:
                    hit_keys.append(k)
                    break
        logger.debug(
            "世界书命中: keys=%s, estimated_tokens=%.1f",
            hit_keys,
            total_tokens,
        )

        for entry in selected:
            # 默认位置为 after_char；后续可读取 entry.position 扩展
            position = getattr(entry, "position", None) or "after_char"
            if position not in sections:
                position = "after_char"
            sections[position].append(entry.content)

        return sections

    def _build_system_prompt(
        self,
        user_profile: Optional[UserProfile],
        diary_context: str,
        warmth_label: str = "友好",
        lore_sections: Optional[Dict[str, List[str]]] = None,
    ) -> str:
        parts = []
        lore_sections = lore_sections or {}

        # before_char 位置的世界书放在角色设定之前
        before_lore = lore_sections.get("before_char", [])
        if before_lore:
            bullets = "\n".join([f"- {c}" for c in before_lore])
            parts.append(f"【世界书】\n{bullets}")

        # 添加当前时间（使用中文星期）
        now = persona_wall_now(self.timezone)
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday = weekdays[now.weekday()]
        time_str = now.strftime(f"%Y年%m月%d日 %H:%M {weekday}")
        parts.append(f"当前时间: {time_str}")

        if self.character.system_prompt:
            parts.append(self.character.system_prompt)
        else:
            if self.character.description:
                parts.append(self.character.description)
            if self.character.personality:
                parts.append(f"性格: {self.character.personality}")
            if self.character.scenario:
                parts.append(f"场景: {self.character.scenario}")

        parts.append(f"你的名字是: {self.character.name}")
        parts.append(f"当前你和用户的关系: {warmth_label}")

        # ── 分段回复引导（仅 chat 路径注入）
        if self.segment_guide and self.segment_guide.enabled:
            sg = self.segment_guide
            guide = (
                f"【回复规则】\n"
                f"你必须使用 send_reply_segment 工具发送回复，不要直接在 content 中输出。\n"
                f"- 每段建议 {sg.target_chars} 字，单段上限 {sg.max_chars} 字\n"
                f"- 单次回复总字数软上限 {sg.soft_limit} 字，硬上限 {sg.hard_limit} 字\n"
                f"- 短句 delay_before 用 {DEFAULT_DELAY_BEFORE} 秒，长句用 2–3 秒\n"
                f"- 回复完成后直接结束，禁止输出任何状态描述（如\"已经回复过了\"、\"回复完成\"等）\n"
                f"\n"
                f"【系统消息说明】\n"
                f"对话中可能出现以 [系统指令] 开头的消息，"
                f"这些是工具调用提醒，不是用户输入。"
                f"看到后直接按指令操作，不要输出任何思考或回应文字。"
            )
            parts.append(guide)

        if user_profile and user_profile.facts:
            facts_text = "\n".join([f"- {k}: {v}" for k, v in user_profile.facts.items()])
            parts.append(f"【你对用户的了解】\n{facts_text}")

        # after_char 位置的世界书（当前默认位置）放在用户了解之后
        after_lore = lore_sections.get("after_char", [])
        if after_lore:
            bullets = "\n".join([f"- {c}" for c in after_lore])
            parts.append(f"【世界书】\n{bullets}")

        if diary_context:
            parts.append(f"【今天发生的事】\n{diary_context}")

        parts.append("请记住用户说过的话，在适当时候提及。不承认自己是AI。")

        return "\n\n".join(parts)

    def _format_private_history(self, history: List[Dict]) -> List[Dict[str, str]]:
        """私聊历史格式化：连续非 assistant 消息合并为单条 user

        连续 user 消息换行拼接，保证 user/assistant 交替输出，满足
        truncate_by_turns 的输入契约。
        """
        if not history:
            return []
        now = persona_wall_now(self.timezone)
        result: List[Dict[str, str]] = []
        buffer: List[Dict] = []

        def flush_buffer():
            if not buffer:
                return
            lines = []
            for m in buffer:
                ts = format_timestamp(m.get("created_at"), now)
                rel = format_relative_time(m.get("created_at"), now)
                extra = f" {rel}" if rel else ""
                prefix = f"[{ts}{extra}] " if ts else ""
                lines.append(f"{prefix}{m['content']}")
            result.append({"role": "user", "content": "\n".join(lines)})
            buffer.clear()

        for msg in history:
            role = msg.get("role", "user")
            if role == "assistant":
                flush_buffer()
                ts = format_timestamp(msg.get("created_at"), now)
                rel = format_relative_time(msg.get("created_at"), now)
                extra = f" {rel}" if rel else ""
                prefix = f"[{ts}{extra}] " if ts else ""
                result.append({
                    "role": "assistant",
                    "content": f"{prefix}{msg['content']}",
                })
            else:
                buffer.append(msg)

        flush_buffer()
        return result

    def _format_group_history(self, history: List[Dict]) -> List[Dict[str, str]]:
        """群聊历史格式化：连续非 assistant 合并为单条 user

        每行格式为 ``[HH:MM] [speaker_name] content``。
        speaker_name 缺失时 fallback 为 ``"系统"``。
        """
        if not history:
            return []
        now = persona_wall_now(self.timezone)
        result = []
        buffer = []

        def flush_buffer():
            if not buffer:
                return
            lines = []
            for m in buffer:
                ts = format_timestamp(m.get("created_at"), now)
                rel = format_relative_time(m.get("created_at"), now)
                extra = f" {rel}" if rel else ""
                ts_prefix = f"[{ts}{extra}] " if ts else ""
                speaker = m.get("speaker_name") or "系统"
                lines.append(f"{ts_prefix}[{speaker}] {m['content']}")
            result.append({"role": "user", "content": "\n".join(lines)})
            buffer.clear()

        for msg in history:
            role = msg.get("role", "user")
            if role == "assistant":
                flush_buffer()
                ts = format_timestamp(msg.get("created_at"), now)
                rel = format_relative_time(msg.get("created_at"), now)
                extra = f" {rel}" if rel else ""
                prefix = f"[{ts}{extra}] " if ts else ""
                result.append({
                    "role": "assistant",
                    "content": f"{prefix}{msg['content']}",
                })
            else:
                buffer.append(msg)

        flush_buffer()
        return result

    def format_history(self, history: List[Dict], is_group: bool) -> List[Dict[str, str]]:
        """格式化历史消息统一入口，根据 is_group 派发私聊/群聊路径"""
        if is_group:
            return self._format_group_history(history)
        return self._format_private_history(history)

    def truncate_by_turns(
        self, history: List[Dict[str, str]], max_turns: int, max_tokens: int
    ) -> List[Dict[str, str]]:
        """按轮次 + token 双重兜底从后往前截断

        一轮 = 一个 user + 一个 assistant 消息对。
        始终保留完整轮次，不拆散对。
        末尾孤立的 user 消息保留。

        输入必须已按 user/assistant 交替排列（开头可能多一个 assistant，末尾可能多一个
        user 或 assistant）。此契约由上游格式化函数 _format_private_history /
        _format_group_history 保证。
        """
        if not history:
            return []

        orphan = None
        work = list(history)

        # 兜底：剥离开头孤立的 assistant（后续配对以 user 开头）
        leading = None
        if work and work[0]["role"] == "assistant":
            leading = work.pop(0)

        if work and work[-1]["role"] == "user":
            orphan = work.pop()

        # 按轮次分组 (user + assistant)
        turns = []
        for i in range(0, len(work) - 1, 2):
            if work[i]["role"] == "user" and work[i + 1]["role"] == "assistant":
                turns.append((work[i], work[i + 1]))

        result = []
        total_tokens = 0.0
        for user_msg, assistant_msg in reversed(turns):
            pair_cost = estimate_tokens(user_msg.get("content", "")) + estimate_tokens(
                assistant_msg.get("content", "")
            )
            if len(result) // 2 >= max_turns:
                break
            if total_tokens + pair_cost > max_tokens and result:
                break
            result.insert(0, assistant_msg)
            result.insert(0, user_msg)
            total_tokens += pair_cost

        if leading:
            result.insert(0, leading)

        # 兜底：末尾孤立 assistant（len(work) 奇数时最后一个元素未被 range 覆盖）
        if len(work) % 2 == 1 and work and work[-1]["role"] == "assistant":
            result.append(work[-1])

        if orphan:
            result.append(orphan)

        return result

    def build_debug_info(
        self,
        short_term_history: List[Dict[str, str]],
        user_profile: Optional[UserProfile] = None,
        diary_context: str = "",
        warmth_label: str = "友好",
        lore_sections: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        system_prompt = self._build_system_prompt(
            user_profile=user_profile,
            diary_context=diary_context,
            warmth_label=warmth_label,
            lore_sections=lore_sections or self.build_lore_text(short_term_history),
        )
        # short_term_history 已由调用方格式化并截断（truncated），直接统计即可
        formatted_chars = sum(len(msg.get("content", "")) for msg in short_term_history)
        profile_text = ""
        if user_profile and user_profile.facts:
            profile_text = "\n".join([f"- {k}: {v}" for k, v in user_profile.facts.items()])
        return {
            "system_prompt_chars": len(system_prompt),
            "short_term_chars": formatted_chars,
            "profile_chars": len(profile_text),
            "diary_chars": len(diary_context),
            "returned_message_count": 1 + len(short_term_history),
        }
