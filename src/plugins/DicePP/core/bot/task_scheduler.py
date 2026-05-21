import asyncio
from typing import Callable, Dict, List, Optional, Union

from utils.logger import dice_log


class TaskScheduler:
    """异步任务调度器，管理待执行任务的注册、处理与清理。

    将原本散落在 Bot 中的 todo_tasks / register_task / process_async_task
    收敛为一个独立可测试的类。
    """

    def __init__(self, error_handler: Callable[[str], List]):
        self._tasks: Dict[Union[Callable, asyncio.Task], Dict] = {}
        self._error_handler = error_handler

    @property
    def pending(self) -> bool:
        return len(self._tasks) > 0

    def schedule(
        self,
        task: Callable,
        is_async: bool = True,
        timeout: float = 10,
        timeout_callback: Optional[Callable] = None,
    ):
        assert is_async or timeout == 0
        self._tasks[task] = {
            "init": False,
            "is_async": is_async,
            "timeout": timeout,
            "callback": timeout_callback,
        }

    def clear_all(self):
        for key in list(self._tasks.keys()):
            if isinstance(key, asyncio.Task):
                key.cancel()
        self._tasks.clear()

    async def process(self, free_time: float) -> List:
        """处理已注册的异步任务，返回完成的 BotCommandBase 列表。

        Args:
            free_time: 最大等待时间（秒）
        Returns:
            已完成任务产生的 BotCommandBase 列表（不再原地修改入参）
        """
        loop = asyncio.get_event_loop()
        results: List = []

        init_task = [(task, info) for task, info in self._tasks.items() if not info["init"]]
        for func, info in init_task:
            func: Callable
            del self._tasks[func]
            if not info["is_async"]:
                dice_log(f"[Async Task] Init Sync: {func.__name__}")

                async def task_wrapper(_func=func):
                    future = loop.run_in_executor(None, _func)
                    await future
                    return future.result()

                task: asyncio.Task = asyncio.create_task(task_wrapper())
            else:
                dice_log(f"[Async Task] Init Async: {func.__name__}")
                task: asyncio.Task = asyncio.create_task(func())
            info["init"] = True
            self._tasks[task] = info

        dice_log(
            f"[Async Task] Try: "
            f"{[(task.get_coro().cr_code.co_name, self._tasks[task]['timeout']) for task in self._tasks.keys()]}"
            f" for {free_time} s"
        )
        try:
            done_tasks, pending_tasks = await asyncio.wait(self._tasks.keys(), timeout=free_time)
            task: asyncio.Task
            for task in done_tasks:
                try:
                    results += task.result()
                except (AttributeError, TypeError, RuntimeError):
                    dice_log(str(self._error_handler("Async Task: CODE114")[0]))
                del self._tasks[task]
                dice_log(f"[Async Task] Finish {task.get_coro().cr_code.co_name}")
            for task in pending_tasks:
                if self._tasks[task]["timeout"] > 0:
                    self._tasks[task]["timeout"] -= free_time
                    if self._tasks[task]["timeout"] < 0:
                        dice_log(f"[Async Task] Timeout: {task.get_coro().cr_code.co_name}")
                        if self._tasks[task]["callback"]:
                            dice_log(f"[Async Task] Timeout callback: {self._tasks[task]['callback'].__name__}")
                            results += self._tasks[task]["callback"]()
                        task.cancel()
                        del self._tasks[task]
        except (AttributeError, TypeError, KeyError, RuntimeError):
            dice_log(str(self._error_handler("Async Task: CODE112")[0]))

        return results
