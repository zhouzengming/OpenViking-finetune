# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
LiteLLM Rerank API Client.
"""

# For logging, use Python's built-in logging
import time
from typing import Any, List, Optional

from openviking.models.rerank.base import RerankBase
from openviking.models.rerank.errors import RerankError, rerank_error_from_exception
from openviking.utils.model_retry import ERROR_CLASS_PERMANENT
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


def _result_field(item: Any, name: str, default: Any = None) -> Any:
    """Read a field from a litellm rerank result item.

    LiteLLM returns result items as objects for some providers (e.g. Cohere)
    and as plain dicts for others (e.g. Voyage), so support both shapes.
    """
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


class LiteLLMRerankClient(RerankBase):
    """
    LiteLLM rerank API client.
    """

    def __init__(
        self,
        api_key: Optional[str],
        api_base: Optional[str],
        model_name: str,
        max_retries: int = 0,
    ):
        """
        Initialize LiteLLM rerank client.

        Args:
            api_key: API key for LiteLLM providers (optional, can come from env)
            api_base: API base for LiteLLM providers (optional, can come from env)
            model_name: Model name to use for reranking
            max_retries: Extra attempts after a retryable failure; 0 disables retrying.
        """
        super().__init__(max_retries=max_retries)
        self.api_key = api_key
        self.api_base = api_base
        self.model_name = model_name
        self.provider = "litellm"

    def _rerank_once(self, query: str, documents: List[str]) -> List[Optional[float]]:
        """One rerank call. Retrying is the base class's job."""
        try:
            import litellm

            started = time.monotonic()
            response = litellm.rerank(
                model=self.model_name,
                query=query,
                documents=documents,
                api_key=self.api_key,
                api_base=self.api_base,
            )

            # Update token usage tracking (estimate from response or input)
            response_dict = (
                response.model_dump() if hasattr(response, "model_dump") else response.__dict__
            )
            self._extract_and_update_token_usage(
                response_dict,
                query,
                documents,
                duration_seconds=time.monotonic() - started,
            )

            results = response.results
            if not results:
                raise RerankError(
                    f"Unexpected response format: {response}",
                    provider=self.provider,
                    error_class=ERROR_CLASS_PERMANENT,
                )

            if len(results) != len(documents):
                raise RerankError(
                    f"Unexpected rerank result length: expected={len(documents)} "
                    f"actual={len(results)}",
                    provider=self.provider,
                    error_class=ERROR_CLASS_PERMANENT,
                )

            for item in results:
                idx = _result_field(item, "index")
                if idx is None or not (0 <= idx < len(documents)):
                    raise RerankError(
                        f"Out-of-bounds or missing index in result: {item}",
                        provider=self.provider,
                        error_class=ERROR_CLASS_PERMANENT,
                    )

            # Results may not be in original order — sort by index
            sorted_results = sorted(results, key=lambda x: _result_field(x, "index"))
            scores = [_result_field(item, "relevance_score", 0.0) for item in sorted_results]

            logger.debug(f"[LiteLLMRerankClient] Reranked {len(documents)} documents")
            return scores

        except RerankError:
            raise
        except Exception as e:
            raise rerank_error_from_exception(e, provider=self.provider) from e

    @classmethod
    def from_config(cls, config) -> Optional["LiteLLMRerankClient"]:
        """
        Create LiteLLMRerankClient from RerankConfig.

        Args:
            config: RerankConfig instance with provider='litellm'

        Returns:
            LiteLLMRerankClient instance or None if config is not available
        """
        if not config or not config.is_available():
            return None
        return cls(
            api_key=config.api_key,
            api_base=config.api_base,
            model_name=config.model,
            max_retries=config.max_retries,
        )
