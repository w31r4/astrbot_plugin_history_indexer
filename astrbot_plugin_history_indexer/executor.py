from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Callable, TypeVar, cast

T = TypeVar("T")
_EXECUTOR: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="history-indexer")
    return _EXECUTOR


async def run_blocking(func: Callable[..., T], *args, **kwargs) -> T:
    loop = asyncio.get_running_loop()
    bound = partial(func, *args, **kwargs)
    return cast(T, await loop.run_in_executor(_get_executor(), bound))


def shutdown_executor():
    global _EXECUTOR
    if _EXECUTOR is not None:
        _EXECUTOR.shutdown(wait=True)
        _EXECUTOR = None


__all__ = ["run_blocking", "shutdown_executor"]
