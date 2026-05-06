"""ObservationBuffer 持久化与操作仓库

将 command.py 中的 observation buffer 状态和方法抽取到独立类，
负责缓冲加载、保存、群聊观察处理。
"""
import json
import time
import logging
from typing import Dict, Optional, Any

from ..data.store import PersonaDataStore
from ..data.persist_keys import PERSONA_SK_OBSERVATION_BUFFERS
from ..life.observation import ObservationBuffer, ObservationExtractor

logger = logging.getLogger("persona.observation_repo")


class ObservationBufferRepository:
    """群聊观察缓冲仓库"""

    def __init__(self, data_store: PersonaDataStore, config):
        self.data_store = data_store
        self.config = config
        self._buffers: Dict[str, ObservationBuffer] = {}
        self._loaded: bool = False
        self._last_persist_monotonic: float = 0.0

    # ── 加载 / 持久化 ─────────────────────────────────────────

    async def ensure_loaded(self) -> None:
        if self._loaded or not self.data_store:
            return
        try:
            raw = await self.data_store.get_setting(PERSONA_SK_OBSERVATION_BUFFERS)
            if not raw:
                return
            try:
                blob = json.loads(raw)
            except json.JSONDecodeError:
                return
            for gid, payload in blob.items():
                if not isinstance(payload, dict):
                    continue
                try:
                    self._buffers[gid] = ObservationBuffer.from_persist_dict(
                        gid,
                        payload,
                        initial_threshold=self.config.observe_initial_threshold,
                        max_threshold=self.config.observe_max_threshold,
                        min_threshold=self.config.observe_min_threshold,
                        max_buffer_size=self.config.observe_max_buffer_size,
                        timezone=self.config.timezone,
                    )
                except Exception:
                    continue
        finally:
            self._loaded = True

    async def persist(self) -> None:
        if not self.data_store:
            return
        data = {gid: buf.to_persist_dict() for gid, buf in self._buffers.items()}
        await self.data_store.set_setting(
            PERSONA_SK_OBSERVATION_BUFFERS,
            json.dumps(data, ensure_ascii=False),
        )

    async def maybe_persist(self, *, force: bool = False) -> None:
        """节流整表 blob 写入；提取观察后应 force=True。"""
        interval = 5.0
        now_m = time.monotonic()
        if (
            not force
            and self._last_persist_monotonic
            and (now_m - self._last_persist_monotonic) < interval
        ):
            return
        await self.persist()
        self._last_persist_monotonic = now_m

    # ── 查询 ──────────────────────────────────────────────────

    def get_status(self, group_id: str) -> Optional[Dict[str, Any]]:
        """返回指定群的观察缓冲状态，无缓冲时返回 None。"""
        buf = self._buffers.get(group_id)
        return buf.get_status() if buf else None

    # ── 群聊观察处理 ──────────────────────────────────────────

    async def handle_observation(
        self,
        group_id: str,
        user_id: str,
        display_name: str,
        msg_str: str,
        event_agent: Optional[Any],
    ) -> None:
        """处理群聊观察：写入共享历史、更新缓冲、触发提取。"""
        if not self.config.observe_group_enabled or not self.data_store:
            return

        # 旁听模式群消息也写入共享历史
        try:
            await self.data_store.add_group_conversation(
                group_id=group_id,
                user_id=user_id,
                role="user",
                content=msg_str,
                display_name=display_name,
            )
        except Exception as e:
            logger.warning(f"旁听群消息写入失败: {e}")

        try:
            await self.ensure_loaded()
            should_extract = False
            try:
                if group_id not in self._buffers:
                    self._buffers[group_id] = ObservationBuffer(
                        group_id=group_id,
                        initial_threshold=self.config.observe_initial_threshold,
                        max_threshold=self.config.observe_max_threshold,
                        min_threshold=self.config.observe_min_threshold,
                        max_buffer_size=self.config.observe_max_buffer_size,
                        timezone=self.config.timezone,
                    )

                buffer = self._buffers[group_id]

                should_extract = buffer.add_message(
                    user_id=user_id,
                    nickname=display_name,
                    content=msg_str,
                )

                if should_extract and event_agent:
                    messages = buffer.get_messages_for_extraction()
                    extractor = ObservationExtractor(
                        event_agent=event_agent,
                        data_store=self.data_store,
                        config=self.config,
                        prune_observations_keep=self.config.observe_max_records,
                    )
                    await extractor.extract_observations(group_id, messages)

                    # 观察触发提取时，更新群内容活跃度（减缓衰减）
                    if self.config.group_activity_enabled:
                        try:
                            await self.data_store.update_group_content(group_id)
                        except Exception as e:
                            logger.warning(f"群内容活跃度更新失败: {e}")

            finally:
                await self.maybe_persist(force=should_extract)

        except Exception as e:
            logger.warning(f"群聊观察失败: {e}")
