"""
上下文构建器

组装四层记忆到 LLM 消息列表
"""
from typing import List, Dict, Optional

from ..character.models import Character
from ..data.models import UserProfile


class ContextBuilder:
    """上下文构建器"""

    def __init__(
        self,
        character: Character,
        max_short_term_chars: int = 1500,
    ):
        self.character = character
        self.max_short_term_chars = max_short_term_chars

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

        system_prompt = self._build_system_prompt(user_profile, diary_context, warmth_label)
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

            system_parts.append(f"近期对话:\n{short_term_text}")

        # 合并为单条 system 消息
        messages.append({"role": "system", "content": "\n\n".join(system_parts)})

        if current_message:
            messages.append({"role": "user", "content": current_message})

        return messages

    def _build_system_prompt(
        self,
        user_profile: Optional[UserProfile],
        diary_context: str,
        warmth_label: str = "友好",
    ) -> str:
        parts = []

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
