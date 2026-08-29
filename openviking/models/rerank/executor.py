# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A thread pool that belongs to rerank alone.

``asyncio.to_thread`` dispatches to the interpreter's default executor, whose
``min(32, cpu+4)`` slots are shared by every blocking call in the process — git
access, storage, crypto, OAuth, the vector backend, and around a hundred other
call sites. Rerank calls are slow, network-bound and bursty, so sharing that
pool meant rerank concurrency was capped by a number nobody chose and rerank
bursts crowded out unrelated work.

The pool is process-wide because a retriever is constructed per search: a
per-retriever pool would spin up and tear down threads on every request.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from openviking_cli.utils import get_logger

logger = get_logger(__name__)

_executor: Optional[ThreadPoolExecutor] = None
_executor_size: Optional[int] = None
_lock = threading.Lock()


def default_rerank_concurrency() -> int:
    """The size of the default executor this pool replaces.

    Matching it means nothing gets slower by default — rerank keeps the same
    ceiling it had, it just no longer shares it.
    """
    return min(32, (os.cpu_count() or 1) + 4)


def get_rerank_executor(max_workers: int = 0) -> ThreadPoolExecutor:
    """Return the process-wide rerank executor, creating it on first use.

    Args:
        max_workers: Pool size; 0 selects :func:`default_rerank_concurrency`.
            Only the first call sizes the pool — resizing a live pool would
            strand in-flight calls, so a later change needs a restart.
    """
    global _executor, _executor_size

    with _lock:
        if _executor is None:
            size = max_workers if max_workers > 0 else default_rerank_concurrency()
            _executor = ThreadPoolExecutor(max_workers=size, thread_name_prefix="ov-rerank")
            _executor_size = size
            logger.info("[RerankExecutor] Started with %s worker threads", size)
        elif max_workers > 0 and max_workers != _executor_size:
            logger.warning(
                "[RerankExecutor] Already running with %s workers; "
                "ignoring request for %s (restart to change)",
                _executor_size,
                max_workers,
            )
        return _executor


def shutdown_rerank_executor() -> None:
    """Drop the pool. Used at shutdown, and between tests."""
    global _executor, _executor_size

    with _lock:
        if _executor is not None:
            _executor.shutdown(wait=False)
            _executor = None
            _executor_size = None


__all__ = [
    "default_rerank_concurrency",
    "get_rerank_executor",
    "shutdown_rerank_executor",
]
