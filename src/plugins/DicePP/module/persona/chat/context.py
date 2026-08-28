"""
上下文构建器

组装四层记忆到 LLM 消息列表
"""
from dataclasses import dataclass
from plugins.DicePP.utils.logger import logger
from typing import List, Dict, Optional, Any

from plugins.DicePP.utils.string import estimate_tokens

from ..character.models import Character
from ..image_cache import ImageCache
from plugins.DicePP.utils.time import wall_now, format_timestamp, format_relative_time
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

        build_static_prompt 使用此方法。
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
        parts.append(
            "请记住玩家说过的话，在适当时候提及。不承认自己是AI。"
            "群聊中的玩家消息格式为[时间] [玩家] [uid: 账号] [昵称: 称呼] 内容；"
            "uid 是稳定的玩家身份，provider name 也由同一 uid 派生，请用 uid 区分玩家，"
            "用昵称称呼玩家。role=assistant 的消息是你自己的发言，正文只附加时间；"
            "历史查询工具的扁平记录用[我]表示你自己的发言，用[事件]表示系统事件。"
            "当前说话者以本轮最后一条[玩家]消息的 uid 为准，"
            "不要根据历史消息里出现过的名字来推断或误认当前说话者的身份。"
            "你可以对往事细节记忆模糊，但不得张冠李戴地误认当前说话者。"
        )
        return parts

    def build_static_prompt(self) -> str:
        """构建静态基座 prompt（不包含动态信息，可被 prompt caching 复用）。"""
        parts = self._render_character_base()

        # 回复长度约束（仅 chat 路径 + segment_guide.enabled），插入到尾部指令之前。
        # 工具提交协议由 Runtime 根据 OutputSpec 统一注入。
        if self.segment_guide and self.segment_guide.enabled:
            sg = self.segment_guide
            guide = (
                f"【回复长度】\n"
                f"- 单段上限 {sg.max_chars} 字，总字数硬上限 {sg.hard_limit} 字"
            )
            parts.insert(-1, guide)

        return "\n\n".join(parts)

    def build(
        self,
        messages: List[Any],
        *,
        static_prompt: str = "",
        notifications: Optional[List[str]] = None,
    ) -> List[Dict[str, str]]:
        """将静态 prompt、通知和 session 消息组装成 LLM 输入。"""
        result: List[Dict[str, str]] = [{"role": "system", "content": static_prompt}]
        result.extend({"role": "user", "content": note} for note in (notifications or []))
        for msg in messages:
            if isinstance(msg, dict):
                result.append({"role": msg["role"], "content": msg["content"]})
            else:
                result.append({"role": msg.role, "content": msg.content})
        return result
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
