# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Why a rerank call failed.

Providers used to collapse every failure into ``return None``: rate limiting,
an expired key, a timeout and a malformed response were indistinguishable. That
left the caller unable to retry what is worth retrying or report what is worth
reporting.

``RerankError`` carries one of ``model_retry``'s error classes so both decisions
become possible. It is an internal channel from a provider to its caller — the
caller still decides what to do, and rerank failures still degrade to vector
scores rather than failing a retrieval.
"""

from __future__ import annotations

from typing import Optional

from openviking.utils.model_retry import (
    ERROR_CLASS_AUTH,
    ERROR_CLASS_PERMANENT,
    ERROR_CLASS_TRANSIENT,
    ERROR_CLASS_UNKNOWN,
    classify_api_error,
)


def _status_code_of(error: BaseException) -> Optional[int]:
    """Read the HTTP status from a requests or httpx error, if it carries one."""
    status = getattr(getattr(error, "response", None), "status_code", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _retry_after_of(error: BaseException) -> Optional[float]:
    """Read the Retry-After hint a throttling response may carry.

    Only the delta-seconds form is honoured; the HTTP-date form is rare in
    practice and parsing it would need clock agreement with the service.
    """
    headers = getattr(getattr(error, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        value = float(headers.get("Retry-After"))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _class_for_status(status_code: int) -> str:
    if status_code in (401, 403):
        return ERROR_CLASS_AUTH
    if status_code == 429 or status_code >= 500:
        return ERROR_CLASS_TRANSIENT
    if 400 <= status_code < 500:
        return ERROR_CLASS_PERMANENT
    return ERROR_CLASS_UNKNOWN


class RerankError(Exception):
    """A rerank call failed, with enough detail to decide what to do about it."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        error_class: str = ERROR_CLASS_UNKNOWN,
        status_code: Optional[int] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.error_class = error_class
        self.status_code = status_code
        self.retry_after = retry_after

    def __str__(self) -> str:
        status = f" status={self.status_code}" if self.status_code is not None else ""
        return f"[{self.provider}] {super().__str__()} (class={self.error_class}{status})"


def rerank_error_from_exception(error: Exception, *, provider: str) -> RerankError:
    """Wrap a provider-side exception, classifying it for the caller.

    The shared text classifier runs first: it recognises content moderation and
    oversized input, which no status code distinguishes from an ordinary 400.
    An HTTP status is the backstop for errors whose message carries no code.
    """
    status_code = _status_code_of(error)
    error_class = classify_api_error(error)
    if error_class == ERROR_CLASS_UNKNOWN and status_code is not None:
        error_class = _class_for_status(status_code)
    return RerankError(
        str(error) or type(error).__name__,
        provider=provider,
        error_class=error_class,
        status_code=status_code,
        retry_after=_retry_after_of(error),
    )


__all__ = ["RerankError", "rerank_error_from_exception"]
