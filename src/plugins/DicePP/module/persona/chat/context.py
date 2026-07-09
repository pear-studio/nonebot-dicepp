"""
上下文构建器

组装四层记忆到 LLM 消息列表
"""
from dataclasses import dataclass
from utils.logger import logger
from typing import List, Dict, Optional, Any, Tuple

from utils.string import estimate_tokens

from ..character.models import Character
from ..data.models import UserProfile
from ..image_cache import ImageCache
from utils.time import wall_now, format_timestamp, format_relative_time
from ..chat.compression import estimate_image_token




def _safe_estimate_tokens(content: Any) -> float:
    """estimate_tokens 防御：content 可能是 str 或 List[dict]（多模态 parts）。"""
    if isinstance(content, str):
        return estimate_tokens(content)
    if isinstance(content, list):
        total = 0.0
        for p in content:
            if isinstance(p, dict):
                if p.get("type") == "image_url":
                    image_url = p.get("image_url", {})
                    url = image_url.get("url", "") if isinstance(image_url, dict) else ""
                    if url.startswith("data:"):
                        total += estimate_image_token(url)
                else:
                    total += estimate_tokens(p.get("text", ""))
        return total
    return 0.0



def _build_image_markers(msg: Dict) -> str:
    """为含图片的历史消息构建 [图片 <hash>] / [表情 <hash>] 标记前缀。"""
    image_meta = msg.get("image_meta")
    if not image_meta:
        return ""
    markers = []
    for entry in image_meta:
        image_hash = entry.get("image_hash")
        if not image_hash:
            # 存量数据无 image_hash，用 url/file 现场计算
            image_hash = ImageCache.compute_image_hash(entry)
            if not image_hash:
                logger.warning(f"[Context] 图片 entry 缺少 url/file，跳过标记: {entry}")
                continue
        sub_type = entry.get("sub_type", "0")
        tag = "表情" if sub_type == "1" else "图片"
        markers.append(f"[{tag} {image_hash}]")
    return "".join(markers) + " "


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

    def _render_character_base(self) -> List[str]:
        """渲染角色卡静态段落（描述/性格/名称/示例对话/尾部指令）。

        build_static_prompt 和 _build_system_prompt 共用此方法。
        """
        parts = []
        if self.character.system_prompt:
            parts.append(self.character.system_prompt)
        else:
            if self.character.description:
                parts.append(self.character.description)
            if self.character.personality:
                parts.append(f"性格: {self.character.personality}")
        parts.append(f"你的名字是: {self.character.name}")
        if self.character.mes_example:
            example = self.character.format_mes_example()
            parts.append(f"示例对话:\n{example}")
        parts.append("请记住用户说过的话，在适当时候提及。不承认自己是AI。")
        return parts

    def build_static_prompt(self) -> str:
        """构建静态基座 prompt（不包含动态信息，可被 prompt caching 复用）。"""
        parts = self._render_character_base()

        # 分段回复引导（仅 chat 路径 + segment_guide.enabled），插入到尾部指令之前
        if self.segment_guide and self.segment_guide.enabled:
            sg = self.segment_guide
            guide = (
                f"【回复规则】\n"
                f"- 调用 send_reply 会发送回复并结束本轮对话\n"
                f"- send_reply_segment 仅在需要分段发送较长回复时使用："
                f"先用它发送前置段，最后调用 send_reply 提交末尾段\n"
                f"- 多数情况下直接调用 send_reply 即可\n"
                f"- 单段上限 {sg.max_chars} 字，总字数硬上限 {sg.hard_limit} 字\n"
                f"- 不要直接输出文本"
            )
            parts.insert(-1, guide)

        return "\n\n".join(parts)

    def build(
        self,
        messages: List[Any] = None,
        *,
        static_prompt: str = "",
        notifications: Optional[List[str]] = None,
        # ── Legacy parameters (deprecated, kept for backward compat in debug paths) ──
        formatted_history: Optional[List[Dict[str, str]]] = None,
        history_dicts: Optional[List[Dict[str, str]]] = None,
        user_profile: Optional[UserProfile] = None,
        diary_context: str = "",
        relation_label: str = "",
    ) -> List[Dict[str, str]]:
        """构建 LLM 消息列表（支持新旧两种调用方式）。

        **新方式（session 模式）**：
        - messages: 已格式化的 session 消息（PersonaSessionMessage 或 dict 列表）
        - static_prompt: 预构建的静态基座
        - notifications: 待注入的动态通知

        **旧方式（legacy，保留给 debug_info）**：
        - formatted_history + history_dicts + user_profile + diary_context + relation_label
        """
        # 新路径：session 模式
        if messages is not None:
            result: List[Dict[str, str]] = []

            # System 消息：静态基座 + 动态关系
            system_content = static_prompt
            if relation_label:
                system_content += f"\n\n当前你和用户的关系: {relation_label}"
            result.append({"role": "system", "content": system_content})

            # 注入通知（独立 user role 消息，在历史消息之前）
            for note in (notifications or []):
                result.append({"role": "user", "content": note})

            # 追加 session 历史消息
            for msg in messages:
                if isinstance(msg, dict):
                    result.append({"role": msg["role"], "content": msg["content"]})
                else:
                    result.append({"role": msg.role, "content": msg.content})

            return result

        # 旧路径：legacy（保留给 build_debug_info 等场景）
        result_legacy = []
        lore_sections = self.build_lore_text(history_dicts or [])
        system_parts = []
        system_prompt = self._build_system_prompt(user_profile, diary_context, relation_label, lore_sections)
        system_parts.append(system_prompt)
        if self.character.mes_example:
            example = self.character.format_mes_example()
            system_parts.append(f"示例对话:\n{example}")
        result_legacy.append({"role": "system", "content": "\n\n".join(system_parts)})
        for msg in (formatted_history or []):
            result_legacy.append({"role": msg["role"], "content": msg["content"]})
        return result_legacy

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
        relation_label: str = "",
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
        now = wall_now(self.timezone)
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        weekday = weekdays[now.weekday()]
        time_str = now.strftime(f"%Y年%m月%d日 %H:%M {weekday}")
        parts.append(f"当前时间: {time_str}")

        # 角色卡基座（描述/性格/场景/名称/示例对话/尾部指令）
        parts.extend(self._render_character_base())

        # 关系标签 — 插入到尾部指令之前
        if relation_label:
            parts.insert(-1, f"当前你和用户的关系: {relation_label}")

        # ── 分段回复引导（仅 chat 路径注入）──
        if self.segment_guide and self.segment_guide.enabled:
            sg = self.segment_guide
            guide = (
                f"【回复规则】\n"
                f"- 调用 send_reply 会发送回复并结束本轮对话\n"
                f"- send_reply_segment 仅在需要分段发送较长回复时使用："
                f"先用它发送前置段，最后调用 send_reply 提交末尾段\n"
                f"- 多数情况下直接调用 send_reply 即可\n"
                f"- 单段上限 {sg.max_chars} 字，总字数硬上限 {sg.hard_limit} 字\n"
                f"- 不要直接输出文本"
            )
            parts.insert(-1, guide)

        if user_profile and user_profile.facts:
            facts_text = "\n".join([f"- {k}: {v}" for k, v in user_profile.facts.items()])
            parts.insert(-1, f"【你对用户的了解】\n{facts_text}")

        # after_char 位置的世界书（当前默认位置）放在用户了解之后
        after_lore = lore_sections.get("after_char", [])
        if after_lore:
            bullets = "\n".join([f"- {c}" for c in after_lore])
            parts.insert(-1, f"【世界书】\n{bullets}")

        if diary_context:
            parts.insert(-1, f"【今天发生的事】\n{diary_context}")

        return "\n\n".join(parts)

    def _format_private_history(self, history: List[Dict]) -> List[Dict[str, str]]:
        """私聊历史格式化：连续非 assistant 消息合并为单条 user

        连续 user 消息换行拼接，保证 user/assistant 交替输出，满足
        truncate_by_turns 的输入契约。
        """
        if not history:
            return []
        now = wall_now(self.timezone)
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
                # 图片标记
                img_prefix = _build_image_markers(m)
                lines.append(f"{prefix}{img_prefix}{m['content']}")
            result.append({"role": "user", "content": "\n".join(lines)})
            buffer.clear()

        for msg in history:
            role = msg.get("role", "user")
            if role == "assistant":
                flush_buffer()
                entry: Dict[str, str] = {
                    "role": "assistant",
                    "content": msg["content"],
                }
                run_id = msg.get("agent_run_id", "")
                if run_id:
                    entry["agent_run_id"] = run_id
                result.append(entry)
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
        now = wall_now(self.timezone)
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
                img_prefix = _build_image_markers(m)
                lines.append(f"{ts_prefix}[{speaker}] {img_prefix}{m['content']}")
            result.append({"role": "user", "content": "\n".join(lines)})
            buffer.clear()

        for msg in history:
            role = msg.get("role", "user")
            if role == "assistant":
                flush_buffer()
                entry: Dict[str, str] = {
                    "role": "assistant",
                    "content": msg["content"],
                }
                run_id = msg.get("agent_run_id", "")
                if run_id:
                    entry["agent_run_id"] = run_id
                result.append(entry)
            else:
                buffer.append(msg)

        flush_buffer()
        return result

    def format_history(self, history: List[Dict], is_group: bool) -> List[Dict[str, str]]:
        """格式化历史消息统一入口，根据 is_group 派发私聊/群聊路径

        Phase M1: 格式化后自动合并同一 agent_run_id 的连续 assistant segments，
        避免 LLM 看到多条连续 assistant 消息破坏 user/assistant 交替契约。
        """
        if is_group:
            formatted = self._format_group_history(history)
        else:
            formatted = self._format_private_history(history)
        return self.merge_same_run_segments(formatted)

    @staticmethod
    def merge_same_run_segments(
        formatted: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        """合并同一 agent_run_id 的连续 assistant segments

        同一 run 的连续 assistant 消息在 DB 中按 segment_index 分开存储，
        读取端应聚合成一条 assistant context message，不破坏 user/assistant 交替。
        如果输入中没有 agent_run_id 标记，则不做合并。

        Args:
            formatted: format_history 初始输出（可能含 agent_run_id 标记）

        Returns:
            合并后的历史列表
        """
        if not formatted:
            return []

        merged: List[Dict[str, str]] = []
        buffer: List[Dict[str, str]] = []
        last_run_id: Optional[str] = None

        def flush_buffer():
            if not buffer:
                return
            if len(buffer) == 1:
                merged.extend(buffer)
            else:
                # 合并多条 assistant 消息为一条
                combined_content = "\n".join(m.get("content", "") for m in buffer)
                merged.append({
                    "role": "assistant",
                    "content": combined_content,
                })
            buffer.clear()

        for entry in formatted:
            role = entry.get("role", "")
            run_id = entry.get("agent_run_id", "")

            if role == "assistant" and run_id:
                # assistant + 有 agent_run_id → 尝试合并
                if run_id == last_run_id:
                    # 同 run，继续缓冲
                    buffer.append(entry)
                else:
                    # 不同 run 或第一个
                    flush_buffer()
                    buffer.append(entry)
                    last_run_id = run_id
            else:
                # 非 assistant 或没有 agent_run_id → 刷新缓冲并原样输出
                flush_buffer()
                last_run_id = None
                merged.append(entry)

        flush_buffer()
        return merged

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
            pair_cost = _safe_estimate_tokens(user_msg.get("content", "")) + _safe_estimate_tokens(
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
        relation_label: str = "",
        lore_sections: Optional[Dict[str, List[str]]] = None,
    ) -> Dict[str, Any]:
        system_prompt = self._build_system_prompt(
            user_profile=user_profile,
            diary_context=diary_context,
            relation_label=relation_label,
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
