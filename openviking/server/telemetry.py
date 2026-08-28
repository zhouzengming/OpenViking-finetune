# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""HTTP router helpers for operation telemetry."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from openviking.server.request_guard import SupportsIsDisconnected, run_guarded
from openviking.telemetry import TelemetryRequest, TelemetrySelection
from openviking.telemetry.execution import (
    TelemetryExecutionResult,
    parse_telemetry_selection,
    run_with_telemetry,
)


def resolve_selection(telemetry: TelemetryRequest) -> TelemetrySelection:
    """Validate a router telemetry request without starting execution."""
    return parse_telemetry_selection(telemetry)


def configured_request_timeout() -> float:
    """Server-wide operation deadline in seconds; 0 means no deadline."""
    from openviking.server.dependencies import get_server_config

    config = get_server_config()
    return getattr(config, "request_timeout_seconds", 0.0) if config is not None else 0.0


async def run_operation(
    *,
    operation: str,
    telemetry: TelemetryRequest,
    fn: Callable[[], Awaitable[Any]],
    http_request: Optional[SupportsIsDisconnected] = None,
) -> TelemetryExecutionResult[Any]:
    """Execute a router operation with request-scoped telemetry.

    Pass ``http_request`` so the operation is abandoned when the client goes
    away; the configured ``server.request_timeout_seconds`` deadline applies
    either way. Guarding runs inside telemetry so an abandoned operation is
    still recorded rather than vanishing.
    """
    timeout = configured_request_timeout()
    return await run_with_telemetry(
        operation=operation,
        telemetry=telemetry,
        fn=lambda: run_guarded(fn, operation=operation, http_request=http_request, timeout=timeout),
    )
