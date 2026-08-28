"""
LLM 调用协调器

同一 target key 在任意时刻只允许一个 LLM 调用在执行，
后续请求排队等待，不得并发。
调用期间到达的新消息被标记为待合并（消息本身已写入数据库）。

buffered merge-retry 状态机：``pending_consumed`` 标记最近一轮 result
是否已被 ``on_result`` 消费。当 ``on_result`` 抛异常时也置 True
（保守策略：异常意味着无法确定是否部分发送，按"已消费"对待，
避免 max_iterations 强制退出时的重复投递）。
"""
from dataclasses import dataclass
from typing import Dict, Optional, Callable, Any, Awaitable, TypeVar, Generic, List, Literal
import asyncio
from plugins.DicePP.utils.logger import logger
from .errors import classify

T = TypeVar("T")


@dataclass
class SubmitResult(Generic[T]):
    """coordinator submit 结果状态"""

    status: Literal["success", "buffered", "failed"]
    value: Optional[T] = None
    error: Optional[Exception] = None

    @classmethod
    def success(cls, value: T) -> "SubmitResult[T]":
        return cls("success", value)

    @classmethod
    def buffered(cls) -> "SubmitResult[T]":
        return cls("buffered", None)

    @classmethod
    def failed(cls, error: Optional[Exception] = None) -> "SubmitResult[T]":
        return cls("failed", None, error)


class LLMCallCoordinator:
    """LLM 调用协调器：按 target_key 串行化调用，支持合并重试。"""

    def __init__(self, max_failures: int = 3, max_iterations: int = 5):
        self.max_failures = max_failures
        self.max_iterations = max_iterations
        self._locks: Dict[str, asyncio.Lock] = {}
        self._executing: Dict[str, bool] = {}
        self._has_buffered: Dict[str, bool] = {}
        self._pending_messages: Dict[str, List[str]] = {}
        # 与 _pending_messages 对齐，保留每次 submit 自己的执行语义。默认（无
        # _coordinator_batch_kind）继续沿用旧的文本合并行为；不同 kind 不跨批合并。
        self._pending_call_fns: Dict[
            str, List[Callable[[List[str]], Awaitable[T]]]
        ] = {}
        # 显式声明 batch kind 的请求需要把执行结果返回给原 submit caller。
        # Future 与 message/call_fn 一一对齐；未声明 kind 的旧调用仍保持立即
        # 返回 buffered 的历史契约。
        self._pending_result_futures: Dict[
            str, List[Optional[asyncio.Future[SubmitResult[T]]]]
        ] = {}

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
        message: str,
        call_fn: Callable[[List[str]], Awaitable[T]],
        on_exhausted: Optional[Callable[[Optional[Exception]], Awaitable[Any]]] = None,
        on_result: Optional[Callable[[T], Awaitable[None]]] = None,
    ) -> SubmitResult[T]:
        """提交一个 LLM 调用请求。

        Args:
            target_key: 目标标识（如 user:123 或 group:456）
            message: 本次请求携带的非空消息
            call_fn: 异步可调用对象，接收本轮待处理的消息列表，执行单次 LLM 调用
            on_exhausted: 最终失败且无成功时的回调，接收最后一次异常
            on_result: 每轮 call_fn 成功后、继续下一轮前调用，用于发送中间结果

        Returns:
            首次 caller: 循环最终成功 → SubmitResult.success(value)
                         循环最终失败 → SubmitResult.failed()
            pending caller（发现 executing=True）→ SubmitResult.buffered()
        """
        logger.debug(
            f"[Persona] coordinator.submit enter: target={target_key}"
            f" message_len={len(message) if message else 0}"
        )
        result_future: Optional[asyncio.Future[SubmitResult[T]]] = None
        if "_coordinator_batch_kind" in getattr(call_fn, "__dict__", {}):
            result_future = asyncio.get_running_loop().create_future()

        lock = self._get_lock(target_key)
        is_driver = False
        async with lock:
            if self._executing.get(target_key, False):
                if target_key not in self._pending_messages:
                    self._pending_messages[target_key] = []
                self._pending_messages[target_key].append(message)
                self._pending_call_fns.setdefault(target_key, []).append(call_fn)
                self._pending_result_futures.setdefault(target_key, []).append(
                    result_future
                )
                self._has_buffered[target_key] = True
                logger.debug(
                    f"[Persona] coordinator: target={target_key} 正在执行中，标记 buffered"
                )
                if result_future is None:
                    return SubmitResult.buffered()
            else:
                self._executing[target_key] = True
                is_driver = True
                if target_key not in self._pending_messages:
                    self._pending_messages[target_key] = []
                self._pending_messages[target_key].append(message)
                self._pending_call_fns.setdefault(target_key, []).append(call_fn)
                self._pending_result_futures.setdefault(target_key, []).append(
                    result_future
                )

        if not is_driver:
            # 显式分批请求在同 scope 的 driver 执行到本请求后，取得自己的结果；
            # 尤其 command 的 failed 必须回到原 caller 以触发领域 fallback。
            assert result_future is not None
            return await result_future

        finalized = False
        try:
            while True:
                result, last_exception = await self._run_loop(
                    target_key, call_fn, on_exhausted, on_result
                )
                # 与 executing=False 原子衔接：若请求恰好在 _run_loop 最后一次
                # buffered 检查之后入队，当前 driver 继续消费，不能留下无人完成的
                # Future。没有等待独立结果的 pending 时，则在同一把锁内正式
                # 释放执行权；旧式 fire-and-buffer 请求仍受原迭代上限约束。
                async with lock:
                    pending_result_futures = self._pending_result_futures.get(
                        target_key, []
                    )
                    if any(future is not None for future in pending_result_futures):
                        continue
                    self._executing.pop(target_key, None)
                    self._has_buffered.pop(target_key, None)
                    self._pending_messages.pop(target_key, None)
                    self._pending_call_fns.pop(target_key, None)
                    self._pending_result_futures.pop(target_key, None)
                    self._locks.pop(target_key, None)
                    finalized = True
                    break
            if result_future is not None and result_future.done():
                return result_future.result()
            return (
                SubmitResult.success(result)
                if result is not None
                else SubmitResult.failed(last_exception)
            )
        finally:
            if not finalized:
                async with lock:
                    self._executing.pop(target_key, None)
                    self._has_buffered.pop(target_key, None)
                    self._pending_messages.pop(target_key, None)
                    self._pending_call_fns.pop(target_key, None)
                    pending_futures = self._pending_result_futures.pop(target_key, [])
                    # 保留 _locks 条目：若此时有等待者持有旧锁引用，pop 会导致
                    # 后续 _get_lock 创建新锁，新旧两把锁下并发访问 _pending_*
                    # 字典，消息丢失或重复处理。
                    for pending_future in pending_futures:
                        if pending_future is not None and not pending_future.done():
                            pending_future.cancel()

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
        on_exhausted: Optional[Callable[[Optional[Exception]], Awaitable[Any]]],
        on_result: Optional[Callable[[T], Awaitable[None]]],
    ) -> tuple[Optional[T], Optional[Exception]]:
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

            # 收集本轮待处理的消息及其提交时执行函数。相同 kind 可以继续合并；
            # chat/command 等异构请求必须分批，避免首个 submit 的闭包消费后续请求。
            lock = self._get_lock(target_key)
            async with lock:
                messages = self._pending_messages.pop(target_key, [])
                call_fns = self._pending_call_fns.pop(target_key, [])
                result_futures = self._pending_result_futures.pop(target_key, [])
                effective_call_fn = call_fn
                batch_result_futures = result_futures
                if call_fns:
                    first_kind = getattr(call_fns[0], "__dict__", {}).get(
                        "_coordinator_batch_kind"
                    )
                    batch_end = 1
                    # 命令携带独立的 speaker/ctx/运行模式，不能像普通聊天文本一样
                    # 跨 submit 合并；否则同一收集轮会由最后一个闭包消费所有命令。
                    while first_kind != "command" and batch_end < len(call_fns):
                        next_kind = getattr(call_fns[batch_end], "__dict__", {}).get(
                            "_coordinator_batch_kind"
                        )
                        if next_kind != first_kind:
                            break
                        batch_end += 1
                    # 未声明 kind 的既有调用保持历史契约：整次执行始终使用首个
                    # submit 的 call_fn。仅显式声明 kind 的请求按该批最新提交者
                    # 选择闭包，以携带正确的 user/ctx。
                    if first_kind is not None:
                        effective_call_fn = call_fns[batch_end - 1]
                    if batch_end < len(call_fns):
                        self._pending_messages[target_key] = messages[batch_end:]
                        self._pending_call_fns[target_key] = call_fns[batch_end:]
                        self._pending_result_futures[target_key] = result_futures[batch_end:]
                        messages = messages[:batch_end]
                        batch_result_futures = result_futures[:batch_end]
                        self._has_buffered[target_key] = True

            try:
                result = await effective_call_fn(messages)
                for future in batch_result_futures:
                    if future is not None and not future.done():
                        future.set_result(SubmitResult.success(result))
                failures = 0
                had_success = True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[Coordinator] call_fn 异常: {type(e).__name__}: {e}")
                if not classify(e).is_retryable:
                    failures = self.max_failures
                else:
                    failures += 1
                result = None
                last_exception = e
                for future in batch_result_futures:
                    if future is not None and not future.done():
                        future.set_result(SubmitResult.failed(e))

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

            # 退出循环
            if failures >= self.max_failures and not had_success:
                await self._invoke_on_exhausted(target_key, on_exhausted, last_exception)
            return result, last_exception

        # 超过最大迭代次数（用户刷屏）
        # 若最后一轮 result 已被 on_result 消费，则不再重复返回
        logger.warning(
            f"coordinator: target={target_key} 超过最大迭代次数 "
            f"({self.max_iterations})，强制退出"
        )
        if not had_success:
            await self._invoke_on_exhausted(target_key, on_exhausted, last_exception)
        return (None if pending_consumed else result), last_exception
