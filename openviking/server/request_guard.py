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

* a disconnect watcher, safe to run here because FastAPI has already consumed
  the request body by the time a handler body executes, so nothing else is
  reading from ``receive``;
* an optional deadline, for clients that stay connected but have given up (and
  for half-open connections, where no disconnect is ever observed).
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, MutableMapping, Optional, Protocol

from openviking_cli.exceptions import ClientDisconnectedError, DeadlineExceededError
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


class SupportsReceive(Protocol):
    """The one piece of ``starlette.requests.Request`` this module needs."""

    async def receive(self) -> MutableMapping[str, Any]: ...


async def _watch_disconnect(http_request: SupportsReceive, operation: str) -> None:
    """Return once the client is gone; never return while it may still be there.

    This awaits ASGI messages instead of polling ``Request.is_disconnected()``.
    ``is_disconnected()`` reads with an immediately-cancelled scope, which works
    against a raw uvicorn receive but silently always reports "connected" once a
    ``BaseHTTPMiddleware`` — that is, any ``@app.middleware("http")``, of which
    this app installs several — replaces the endpoint's receive with its
    ``receive_or_disconnect`` wrapper: that wrapper needs at least one scheduling
    round to hand over a queued message, and the cancelled scope never gives it
    one. Awaiting the message works through both paths.

    Only safe because callers wire this to routes whose body FastAPI has already
    consumed; on a streaming endpoint it would eat the body the handler needs.
    """
    while True:
        try:
            message = await http_request.receive()
        except Exception as e:  # noqa: BLE001
            # Losing the ability to read must not abort a live request. Block
            # forever instead, so this watcher can never win the race, and let
            # the deadline be the backstop.
            logger.debug("[RequestGuard] Disconnect watch failed for %s: %s", operation, e)
            await asyncio.Event().wait()
        if message.get("type") == "http.disconnect":
            return


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
    http_request: Optional[SupportsReceive] = None,
    timeout: Optional[float] = None,
) -> Any:
    """Run ``fn`` but abandon it once the client leaves or the deadline passes.

    Args:
        fn: Zero-argument factory returning the awaitable to guard.
        operation: Operation name used in logs and error details.
        http_request: Request to watch for disconnects; None disables that guard.
        timeout: Deadline in seconds; None or <= 0 disables that guard.

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
        watcher = asyncio.ensure_future(_watch_disconnect(http_request, operation))
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
