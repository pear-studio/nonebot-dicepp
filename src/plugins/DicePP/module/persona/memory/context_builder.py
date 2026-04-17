"""
上下文构建器

组装四层记忆到 LLM 消息列表
"""
import logging
from typing import List, Dict, Optional, Any

from ..character.models import Character
from ..data.models import UserProfile
from ..wall_clock import persona_wall_now

logger = logging.getLogger("persona.context_builder")


def _estimate_tokens(text: str) -> float:
    """粗略估算 token 数：中文字符按 1 token，其余按每 4 字符 1 token"""
    cn_chars = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other_chars = len(text) - cn_chars
    return cn_chars + other_chars / 4


class ContextBuilder:
    """上下文构建器"""

    def __init__(
        self,
        character: Character,
        max_short_term_chars: int = 1500,
        timezone: str = "Asia/Shanghai",
        lore_token_budget: int = 300,
    ):
        self.character = character
        self.max_short_term_chars = max_short_term_chars
        self.timezone = timezone
        self.lore_token_budget = lore_token_budget

    def build(
        self,
        short_term_history: List[Dict[str, str]],
        user_profile: Optional[UserProfile] = None,
        diary_context: str = "",
        current_message: str = "",
        warmth_label: str = "友好",
    ) -> List[Dict[str, str]]:
        messages = []

        # 合并所有 system 内容为一条（某些提供商如 MiniMax 不支持多条 system 消息）
        system_parts = []

        # 世界书扫描（按位置分类，为后续 LoreEntry.position 扩展留接口）
        lore_sections = self._build_lore_text(short_term_history, current_message)

        system_prompt = self._build_system_prompt(user_profile, diary_context, warmth_label, lore_sections)
        system_parts.append(system_prompt)

        if self.character.mes_example:
            example = self.character.format_mes_example()
            system_parts.append(f"示例对话:\n{example}")

        # 按对话轮次截断，保留完整的 user-assistant 对
        truncated_history = self._truncate_by_turns(short_term_history, self.max_short_term_chars)
        short_term_text = self._format_short_term(truncated_history)
        if short_term_text:
            # 如果发生了截断（返回的历史比原历史短），添加省略标记
            if len(truncated_history) < len(short_term_history):
                short_term_text = "...（前文省略）\n" + short_term_text
                original_chars = sum(len(m.get("content", "")) for m in short_term_history)
                kept_chars = sum(len(m.get("content", "")) for m in truncated_history)
                logger.debug(
                    f"history_truncated turns_before={len(short_term_history)} "
                    f"turns_after={len(truncated_history)} "
                    f"content_chars_before={original_chars} "
                    f"content_chars_after={kept_chars}"
                )

            system_parts.append(f"近期对话:\n{short_term_text}")

        # 合并为单条 system 消息
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})

        if current_message:
            messages.append({"role": "user", "content": current_message})

        return messages

    def _build_lore_text(
        self,
        short_term_history: List[Dict[str, str]],
        current_message: str,
    ) -> Dict[str, List[str]]:
        """扫描文本并返回按位置分类的世界书内容

        返回结构为 {"before_char": [...], "after_char": [...]}，
        即使目前 LoreEntry 没有 position 字段，也为后续扩展留接口。
        默认所有条目归入 "after_char"（与当前硬编码位置一致）。
        """
        sections: Dict[str, List[str]] = {"before_char": [], "after_char": []}
        if not self.character or not self.character.character_book:
            return sections

        texts_to_scan = [current_message] if current_message is not None else []
        for msg in short_term_history:
            texts_to_scan.append(msg.get("content", ""))

        matched = self.character.search_lore_entries(texts_to_scan)

        if not matched:
            return sections

        # 按优先级降序排列，数值越高越优先注入
        matched.sort(key=lambda e: e.order, reverse=True)

        # Token 预算控制（当前为字符估算值，非精确 token）
        budget = self.lore_token_budget
        total_tokens = 0.0
        selected = []
        for entry in matched:
            cost = _estimate_tokens(entry.content)
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

    def _format_short_term(self, history: List[Dict[str, str]]) -> str:
        """格式化短期记忆"""
        lines = []
        for msg in history:
            role = "用户" if msg["role"] == "user" else self.character.name
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)

    def _truncate_by_turns(self, history: List[Dict[str, str]], max_chars: int) -> List[Dict[str, str]]:
        """按对话轮次截断，从后往前保留完整的 user-assistant 对

        避免截断在对话中间，保持上下文完整性。
        """
        if not history:
            return []

        # 从后往前累计
        result = []
        total_chars = 0

        for msg in reversed(history):
            msg_chars = len(msg.get("content", ""))
            # 如果超限制且已保留至少一条，停止
            if total_chars + msg_chars > max_chars and result:
                break
            result.insert(0, msg)  # 插入头部保持顺序
            total_chars += msg_chars

        return result

    def build_debug_info(
        self,
        short_term_history: List[Dict[str, str]],
        user_profile: Optional[UserProfile] = None,
        diary_context: str = "",
        warmth_label: str = "友好",
        lore_sections: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        truncated = self._truncate_by_turns(short_term_history, self.max_short_term_chars)
        system_prompt = self._build_system_prompt(
            user_profile=user_profile,
            diary_context=diary_context,
            warmth_label=warmth_label,
            lore_sections=lore_sections or self._build_lore_text(short_term_history, ""),
        )
        short_term_text = self._format_short_term(truncated)
        profile_text = ""
        if user_profile and user_profile.facts:
            profile_text = "\n".join([f"- {k}: {v}" for k, v in user_profile.facts.items()])
        return {
            "system_prompt_chars": len(system_prompt),
            "short_term_chars": len(short_term_text),
            "profile_chars": len(profile_text),
            "diary_chars": len(diary_context),
            "returned_message_count": 1 + len(truncated),  # system(1) + short_term(len(truncated))
        }
