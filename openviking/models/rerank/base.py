# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
RerankBase: Base class for all rerank clients.

Provides common token usage tracking functionality.
"""

import logging
import random
import time
from typing import Any, Dict, List, Optional

from openviking.models.rerank.errors import RerankError
from openviking.utils.model_retry import (
    ERROR_CLASS_QUOTA_EXCEEDED,
    ERROR_CLASS_TRANSIENT,
)
from openviking.utils.token_estimation import estimate_text_tokens
from openviking_cli.utils import get_logger

_token_tracker_instance = None
logger = get_logger(__name__)

# Retrying happens inside a live search, so the ceiling is low on purpose: a
# provider that is still throttling after a few seconds is better answered with
# vector scores than with a search that never returns.
RERANK_RETRY_BASE_DELAY_SECONDS = 0.5
RERANK_RETRY_MAX_DELAY_SECONDS = 8.0

# Retrying anything else only burns the request's remaining time: a rejected
# credential, a malformed request and a moderation refusal all fail again
# identically. "unknown" is treated the same way — an unclassifiable failure is
# not evidence that waiting helps.
RETRYABLE_RERANK_ERROR_CLASSES = frozenset({ERROR_CLASS_TRANSIENT, ERROR_CLASS_QUOTA_EXCEEDED})


def _get_token_tracker():
    """Lazy import to avoid circular dependency. Returns shared singleton instance."""
    global _token_tracker_instance
    if _token_tracker_instance is None:
        from openviking.models.vlm.token_usage import TokenUsageTracker

        _token_tracker_instance = TokenUsageTracker()
    return _token_tracker_instance


def get_shared_rerank_token_usage() -> Dict[str, Any]:
    """
    Return the process-wide rerank token usage snapshot.

    This reads the shared singleton `TokenUsageTracker` used by all rerank clients.
    It exists so observability paths (e.g. Prometheus scrape) can access rerank usage
    without constructing provider clients that may allocate network resources.
    """
    return _get_token_tracker().to_dict()


class RerankBase:
    """Base class for all rerank clients

    Provides common token usage tracking functionality.
    """

    def __init__(self, max_retries: int = 0):
        """Initialize rerank client with token tracking.

        Args:
            max_retries: Extra attempts after a retryable failure. 0 keeps the
                historical behaviour of giving up immediately.
        """
        # Token usage tracking
        self._token_tracker = _get_token_tracker()
        self.max_retries = max(0, int(max_retries))

    def rerank_batch(self, query: str, documents: List[str]) -> List[Optional[float]]:
        """Rerank ``documents`` against ``query``, retrying what is worth retrying.

        Args:
            query: Query text
            documents: List of document texts to rank

        Returns:
            One entry per input document, in input order: the rerank score, or
            None where the service returned no score for that document.

        Raises:
            RerankError: Every attempt failed, or the failure was not one that
                retrying can fix.
        """
        if not documents:
            return []

        attempt = 0
        while True:
            try:
                return self._rerank_once(query, documents)
            except RerankError as e:
                if attempt >= self.max_retries:
                    raise
                if e.error_class not in RETRYABLE_RERANK_ERROR_CLASSES:
                    raise
                delay = self._retry_delay(attempt, e.retry_after)
                logger.warning(
                    "[%s] Rerank attempt %s/%s failed (%s), retrying in %.2fs: %s",
                    getattr(self, "provider", "rerank"),
                    attempt + 1,
                    self.max_retries + 1,
                    e.error_class,
                    delay,
                    e,
                )
                self._count_retry(e.error_class)
                time.sleep(delay)
                attempt += 1

    def _rerank_once(self, query: str, documents: List[str]) -> List[Optional[float]]:
        """Perform one provider call. Implemented by each provider."""
        raise NotImplementedError

    @staticmethod
    def _retry_delay(attempt: int, retry_after: Optional[float]) -> float:
        """How long to wait before the next attempt.

        A Retry-After hint is the service stating its own terms, so it is used
        verbatim — capped, because a search cannot wait out an hour-long
        cooldown. Otherwise back off exponentially with jitter, so concurrent
        searches that were throttled together do not retry in lockstep.
        """
        if retry_after is not None:
            return min(float(retry_after), RERANK_RETRY_MAX_DELAY_SECONDS)
        ceiling = min(
            RERANK_RETRY_BASE_DELAY_SECONDS * (2**attempt), RERANK_RETRY_MAX_DELAY_SECONDS
        )
        return ceiling * random.uniform(0.5, 1.0)

    @staticmethod
    def _count_retry(error_class: str) -> None:
        try:
            from openviking.telemetry import get_current_telemetry

            telemetry = get_current_telemetry()
            telemetry.count("rerank.retries", 1)
            telemetry.count(f"rerank.retries_by_class.{error_class}", 1)
        except Exception as e:  # noqa: BLE001
            # Telemetry must never break rerank execution.
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("rerank.retry telemetry emit failed: %s", e)

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count with the shared mixed-language fallback."""
        return max(1, estimate_text_tokens(text))

    def update_token_usage(
        self,
        model_name: str,
        provider: str,
        prompt_tokens: int,
        completion_tokens: int,
        duration_seconds: float = 0.0,
    ) -> None:
        """Update token usage

        Args:
            model_name: Model name
            provider: Provider name (vikingdb, openai, cohere, litellm, etc.)
            prompt_tokens: Number of input tokens
            completion_tokens: Number of output tokens
            duration_seconds: Wall-clock duration of the rerank provider call in seconds
        """
        self._token_tracker.update(
            model_name=model_name,
            provider=provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        try:
            from openviking.telemetry import get_current_telemetry

            get_current_telemetry().record_token_usage(
                "rerank",
                int(prompt_tokens),
                int(completion_tokens),
                stage="rerank",
            )
        except Exception as e:
            # Telemetry must never break rerank execution.
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "rerank.update_token_usage telemetry emit failed provider=%s model_name=%s err=%s: %s",
                    provider,
                    model_name,
                    type(e).__name__,
                    e,
                )
        try:
            from openviking.metrics.datasources import RerankEventDataSource
            from openviking.observability.context import get_root_observability_context

            root_context = get_root_observability_context()

            RerankEventDataSource.record_call(
                provider=str(provider),
                model_name=str(model_name),
                duration_seconds=max(float(duration_seconds), 0.0),
                prompt_tokens=int(prompt_tokens),
                completion_tokens=int(completion_tokens),
                account_id=root_context.account_id if root_context is not None else None,
            )
        except Exception as e:
            # Metrics must never break rerank execution.
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "rerank.update_token_usage metrics emit failed provider=%s model_name=%s err=%s: %s",
                    provider,
                    model_name,
                    type(e).__name__,
                    e,
                )

    def _extract_and_update_token_usage(
        self,
        response_data: dict,
        query: str,
        documents: list,
        duration_seconds: float = 0.0,
    ) -> None:
        """Extract and update token usage from API response.

        Args:
            response_data: Raw API response dict
            query: Query text (for estimation if needed)
            documents: List of documents (for estimation if needed)
            duration_seconds: Wall-clock duration of the rerank provider call in seconds
        """
        prompt_tokens = 0
        completion_tokens = 0

        # Try to extract token usage from response meta (Coherever format)
        meta = response_data.get("meta", {})
        if isinstance(meta, dict):
            billed_units = meta.get("billed_units", {})
            if isinstance(billed_units, dict):
                prompt_tokens = billed_units.get("input_tokens", 0)
                completion_tokens = billed_units.get("output_tokens", 0)

        # If no token info in response, estimate based on text
        if prompt_tokens == 0 and completion_tokens == 0:
            prompt_tokens = self._estimate_tokens(query)
            for doc in documents:
                prompt_tokens += self._estimate_tokens(str(doc))
            # Rerank typically doesn't generate output tokens, just scores
            completion_tokens = 0

        # Get model name
        model_name = getattr(self, "model_name", None) or getattr(self, "model", None) or "unknown"

        self.update_token_usage(
            model_name=model_name,
            provider=self.provider,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            duration_seconds=duration_seconds,
        )

    def get_token_usage(self) -> Dict[str, Any]:
        """Get token usage

        Returns:
            Dict[str, Any]: Token usage dictionary
        """
        return self._token_tracker.to_dict()

    def reset_token_usage(self) -> None:
        """Reset token usage"""
        self._token_tracker.reset()
