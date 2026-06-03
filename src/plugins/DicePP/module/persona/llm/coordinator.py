"""
LLM 调用协调器

同一 target key 在任意时刻只允许一个 LLM 调用在执行，
后续 chat / share 请求排队等待，不得并发。
调用期间到达的新消息被标记为待合并（消息本身已写入数据库）。

buffered merge-retry 状态机：``pending_consumed`` 标记最近一轮 result
是否已被 ``on_result`` 消费。当 ``on_result`` 抛异常时也置 True
（保守策略：异常意味着无法确定是否部分发送，按"已消费"对待，
避免 max_iterations 强制退出时的重复投递）。
"""
from dataclasses import dataclass
from typing import Dict, Optional, Callable, Any, Awaitable, TypeVar, Generic, List, Literal
import asyncio
from nonebot.log import logger
from utils.logger import _request_id_var
from .errors import classify

T = TypeVar("T")


@dataclass
class SubmitResult(Generic[T]):
    """coordinator submit 结果状态"""

    status: Literal["success", "buffered", "failed"]
    value: Optional[T] = None

    @classmethod
    def success(cls, value: T) -> "SubmitResult[T]":
        return cls("success", value)

    @classmethod
    def buffered(cls) -> "SubmitResult[T]":
        return cls("buffered", None)

    @classmethod
    def failed(cls) -> "SubmitResult[T]":
        return cls("failed", None)


class LLMCallCoordinator:
    """LLM 调用协调器：按 target_key 串行化调用，支持合并重试。"""

    def __init__(self, max_failures: int = 3, max_iterations: int = 5):
        self.max_failures = max_failures
        self.max_iterations = max_iterations
        self._locks: Dict[str, asyncio.Lock] = {}
        self._executing: Dict[str, bool] = {}
        self._has_buffered: Dict[str, bool] = {}
        self._pending_messages: Dict[str, List[str]] = {}

    def _get_lock(self, target_key: str) -> asyncio.Lock:
        """按 target_key 懒创建 asyncio.Lock（复用同一对象）。

        注意：无 pending 消息时 submit() 的 finally 会清理 lock；
        _executing 标志保证串行语义，与 lock identity 无关。
        asyncio.Lock 对象本身开销极小，target_key 数量受限于实际用户/群数量，
        长期运行无实际内存压力。
        """
        if target_key not in self._locks:
            self._locks[target_key] = asyncio.Lock()
            logger.debug(
                f"coordinator: 新建 lock for target={target_key}, "
                f"当前 _locks 大小={len(self._locks)}"
            )
        return self._locks[target_key]

    async def submit(
        self,
        target_key: str,
        message: Optional[str],
        call_fn: Callable[[List[str]], Awaitable[T]],
        continue_on_buffered: bool = True,
        on_exhausted: Optional[Callable[[Optional[Exception]], Awaitable[Any]]] = None,
        on_result: Optional[Callable[[T], Awaitable[None]]] = None,
    ) -> SubmitResult[T]:
        """提交一个 LLM 调用请求。

        Args:
            target_key: 目标标识（如 user:123 或 group:456）
            message: 本次请求携带的消息（chat 路径为用户输入；share/miss 路径可为 None）
            call_fn: 异步可调用对象，接收本轮待处理的消息列表，执行单次 LLM 调用
            continue_on_buffered: True=chat 路径（成功后检查 buffered 继续循环）;
                                 False=share 路径（成功直接退出）
            on_exhausted: 最终失败且无成功时的回调，接收最后一次异常（chat 路径发送兜底文案）
            on_result: 每轮 call_fn 成功后、继续下一轮前调用，用于发送中间结果

        Returns:
            首次 caller: 循环最终成功 → SubmitResult.success(value)
                         循环最终失败 → SubmitResult.failed()
            pending caller（发现 executing=True）→ SubmitResult.buffered()
        """
        rid = _request_id_var.get()
        logger.bind(request_id=rid).debug(
            f"[Persona] coordinator.submit enter: target={target_key}"
            f" message_len={len(message) if message else 0}"
        )
        lock = self._get_lock(target_key)
        async with lock:
            if self._executing.get(target_key, False):
                if message is not None:
                    if target_key not in self._pending_messages:
                        self._pending_messages[target_key] = []
                    self._pending_messages[target_key].append(message)
                self._has_buffered[target_key] = True
                logger.bind(request_id=rid).debug(
                    f"[Persona] coordinator: target={target_key} 正在执行中，标记 buffered"
                )
                return SubmitResult.buffered()
            self._executing[target_key] = True
            if message is not None:
                if target_key not in self._pending_messages:
                    self._pending_messages[target_key] = []
                self._pending_messages[target_key].append(message)

        try:
            result = await self._run_loop(
                target_key, call_fn, continue_on_buffered, on_exhausted, on_result
            )
            return SubmitResult.success(result) if result is not None else SubmitResult.failed()
        finally:
            async with lock:
                self._executing.pop(target_key, None)
                self._has_buffered.pop(target_key, None)
                if not self._pending_messages.get(target_key):
                    self._pending_messages.pop(target_key, None)
                    self._locks.pop(target_key, None)

    async def _invoke_on_exhausted(
        self,
        target_key: str,
        on_exhausted: Optional[Callable[[Optional[Exception]], Awaitable[Any]]],
        last_exception: Optional[Exception],
    ) -> None:
        """调用 on_exhausted 回调并吞掉其异常。"""
        if not on_exhausted:
            return
        try:
            await on_exhausted(last_exception)
        except Exception:
            logger.exception(
                f"coordinator: target={target_key} on_exhausted 回调异常"
            )

    async def _run_loop(
        self,
        target_key: str,
        call_fn: Callable[[List[str]], Awaitable[T]],
        continue_on_buffered: bool,
        on_exhausted: Optional[Callable[[Optional[Exception]], Awaitable[Any]]],
        on_result: Optional[Callable[[T], Awaitable[None]]],
    ) -> Optional[T]:
        """重试/合并循环。

        每次 call_fn 成功后重置 failures；连续失败 max_failures 次后退出。
        为防止用户刷屏导致无限循环，单次 submit 最多迭代 max_iterations 次。
        """
        failures = 0
        had_success = False
        result: Optional[T] = None
        iterations = 0
        last_exception: Optional[Exception] = None
        pending_consumed = False  # 本轮 result 是否已被 on_result 消费

        while iterations < self.max_iterations:
            iterations += 1
            pending_consumed = False

            # 收集本轮待处理的消息
            lock = self._get_lock(target_key)
            async with lock:
                messages = self._pending_messages.pop(target_key, [])

            try:
                result = await call_fn(messages)
                failures = 0
                had_success = True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                from utils.logger import dice_log as _dlog
                _dlog(f"[Coordinator] call_fn 异常: {type(e).__name__}: {e}")
                if not classify(e).is_retryable:
                    failures = self.max_failures
                else:
                    failures += 1
                result = None
                last_exception = e

            lock = self._get_lock(target_key)
            async with lock:
                has_buffered = self._has_buffered.pop(target_key, False)

            if has_buffered and failures < self.max_failures:
                if on_result and result is not None:
                    try:
                        await on_result(result)
                        pending_consumed = True
                    except Exception:
                        logger.exception(
                            f"coordinator: target={target_key} on_result 回调异常"
                        )
                        # 保守策略：异常意味着无法确定 result 是否已部分发送，
                        # 按"已消费"对待，避免后续 max_iterations 强制退出时重复投递。
                        pending_consumed = True
                logger.debug(
                    f"coordinator: target={target_key} buffered，继续合并重试 "
                    f"(failures={failures}, iterations={iterations})"
                )
                continue

            # share 路径：成功且无缓冲时直接退出（失败走下方 on_exhausted 逻辑）
            if not continue_on_buffered and had_success:
                return result

            # 退出循环
            if failures >= self.max_failures and not had_success:
                await self._invoke_on_exhausted(target_key, on_exhausted, last_exception)
            return result

        # 超过最大迭代次数（用户刷屏）
        # 若最后一轮 result 已被 on_result 消费，则不再重复返回
        logger.warning(
            f"coordinator: target={target_key} 超过最大迭代次数 "
            f"({self.max_iterations})，强制退出"
        )
        if not had_success:
            await self._invoke_on_exhausted(target_key, on_exhausted, last_exception)
        return None if pending_consumed else result
