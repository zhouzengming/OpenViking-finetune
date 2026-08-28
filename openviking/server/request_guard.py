# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Stop server-side work that nobody is waiting for any more.

Starlette runs a handler to completion regardless of the client: its
``request_response`` wrapper simply awaits the endpoint, and uvicorn only marks
the connection as disconnected without cancelling the ASGI task. An application
learns about a disconnect solely by polling ``Request.is_disconnected()``.
Combined with uvicorn being started without a request timeout, an abandoned
retrieval keeps consuming rerank quota and GPU time until it happens to finish.

This module races the actual work against two guards:

* a disconnect poller, safe to run here because FastAPI has already consumed the
  request body by the time a handler body executes, so nothing else is reading
  from ``receive``;
* an optional deadline, for clients that stay connected but have given up (and
  for half-open connections, where no disconnect is ever observed).
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional, Protocol

from openviking_cli.exceptions import ClientDisconnectedError, DeadlineExceededError
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_POLL_SECONDS = 1.0


class SupportsIsDisconnected(Protocol):
    """The one piece of ``starlette.requests.Request`` this module needs."""

    async def is_disconnected(self) -> bool: ...


async def _watch_disconnect(
    http_request: SupportsIsDisconnected, poll_seconds: float, operation: str
) -> None:
    """Return once the client is gone; never return while it may still be there."""
    while True:
        try:
            if await http_request.is_disconnected():
                return
        except Exception as e:  # noqa: BLE001
            # Losing the ability to poll must not abort a live request, so treat
            # an unreadable connection as still connected and let the deadline
            # be the backstop.
            logger.debug("[RequestGuard] Disconnect poll failed for %s: %s", operation, e)
        await asyncio.sleep(poll_seconds)


async def _cancel_and_drain(*tasks: Optional["asyncio.Task[Any]"]) -> None:
    """Cancel tasks and await them so their finally blocks actually run."""
    pending = [task for task in tasks if task is not None and not task.done()]
    if not pending:
        return
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


async def run_guarded(
    fn: Callable[[], Awaitable[Any]],
    *,
    operation: str,
    http_request: Optional[SupportsIsDisconnected] = None,
    timeout: Optional[float] = None,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
) -> Any:
    """Run ``fn`` but abandon it once the client leaves or the deadline passes.

    Args:
        fn: Zero-argument factory returning the awaitable to guard.
        operation: Operation name used in logs and error details.
        http_request: Request to poll for disconnects; None disables that guard.
        timeout: Deadline in seconds; None or <= 0 disables that guard.
        poll_seconds: Disconnect poll interval.

    Returns:
        Whatever ``fn`` returns, with its exceptions propagated unchanged.

    Raises:
        ClientDisconnectedError: The client went away first.
        DeadlineExceededError: The deadline passed first.
    """
    deadline = timeout if timeout and timeout > 0 else None
    if http_request is None and deadline is None:
        return await fn()

    work = asyncio.ensure_future(fn())
    watcher: Optional[asyncio.Task[None]] = None
    waiters: set[Any] = {work}
    if http_request is not None:
        watcher = asyncio.ensure_future(_watch_disconnect(http_request, poll_seconds, operation))
        waiters.add(watcher)

    try:
        done, _pending = await asyncio.wait(
            waiters, timeout=deadline, return_when=asyncio.FIRST_COMPLETED
        )
    except asyncio.CancelledError:
        await _cancel_and_drain(work, watcher)
        raise

    if work in done:
        await _cancel_and_drain(watcher)
        return work.result()

    await _cancel_and_drain(work, watcher)

    if watcher is not None and watcher in done:
        logger.info("[RequestGuard] Client disconnected, abandoned %s", operation)
        raise ClientDisconnectedError(operation)

    logger.warning("[RequestGuard] Deadline of %ss exceeded, abandoned %s", deadline, operation)
    raise DeadlineExceededError(operation, deadline)
