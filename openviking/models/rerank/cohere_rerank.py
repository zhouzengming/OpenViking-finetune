# Copyright (c) 2026 Antigravity / Dico Angelo
# SPDX-License-Identifier: AGPL-3.0
"""
Cohere Rerank API Client.

Drop-in replacement for VikingDB RerankClient, using Cohere's Rerank v3.5 API.
Same interface: rerank_batch(query, documents) -> List[Optional[float]]
"""

# For logging, use Python's built-in logging
import time
from typing import List, Optional

import httpx

from openviking.models.rerank.base import RerankBase
from openviking.models.rerank.errors import RerankError, rerank_error_from_exception
from openviking.utils.model_retry import ERROR_CLASS_PERMANENT
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class CohereRerankClient(RerankBase):
    """Cohere Rerank API client — same interface as VikingDB RerankClient."""

    def __init__(
        self,
        api_key: str,
        model: str = "rerank-v3.5",
        api_base: str = "https://api.cohere.com",
    ):
        super().__init__()
        self.api_key = api_key
        self.model = model
        self.api_base = api_base.rstrip("/")
        self.provider = "cohere"
        self._client = httpx.Client(
            base_url=self.api_base,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def rerank_batch(self, query: str, documents: List[str]) -> List[Optional[float]]:
        """
        Rerank documents against a query using Cohere Rerank API.

        Args:
            query: Query text
            documents: List of document texts to rank

        Returns:
            One entry per input document, in input order: the relevance score
            (0-1), or None where Cohere returned no score for that document.

        Raises:
            RerankError: The call failed; ``error_class`` says whether retrying
                could help.
        """
        if not documents:
            return []

        try:
            started = time.monotonic()
            resp = self._client.post(
                "/v2/rerank",
                json={
                    "model": self.model,
                    "query": query,
                    "documents": documents,
                    "top_n": len(documents),
                    "return_documents": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            # Update token usage tracking
            self._extract_and_update_token_usage(
                data,
                query,
                documents,
                duration_seconds=time.monotonic() - started,
            )

            # Cohere returns results sorted by score desc with an index field,
            # so map back to original order. A document Cohere left out stays
            # None: the caller keeps its vector score rather than being handed
            # a fabricated 0.0 that any threshold would prune.
            results = data.get("results", [])
            if len(results) != len(documents):
                logger.warning(
                    "[CohereRerank] Sparse rerank results: expected=%s actual=%s",
                    len(documents),
                    len(results),
                )

            scores: List[Optional[float]] = [None] * len(documents)
            for result in results:
                idx = result.get("index") if isinstance(result, dict) else None
                # bool is an int subclass, and `scores[True]` would silently
                # score document 1; a negative index would silently score a
                # document counted from the end.
                if not isinstance(idx, int) or isinstance(idx, bool):
                    raise RerankError(
                        f"Non-integer index in result: {result}",
                        provider=self.provider,
                        error_class=ERROR_CLASS_PERMANENT,
                    )
                if not (0 <= idx < len(documents)):
                    raise RerankError(
                        f"Out-of-bounds index in result: {result}",
                        provider=self.provider,
                        error_class=ERROR_CLASS_PERMANENT,
                    )
                if "relevance_score" not in result:
                    raise RerankError(
                        f"Missing relevance_score in result: {result}",
                        provider=self.provider,
                        error_class=ERROR_CLASS_PERMANENT,
                    )
                scores[idx] = result["relevance_score"]

            logger.debug(f"[CohereRerank] Reranked {len(documents)} documents")
            return scores

        except RerankError:
            raise
        except Exception as e:
            raise rerank_error_from_exception(e, provider=self.provider) from e

    def close(self):
        self._client.close()

    @classmethod
    def from_config(cls, config) -> Optional["CohereRerankClient"]:
        """
        Create CohereRerankClient from RerankConfig.

        Args:
            config: RerankConfig instance with provider='cohere'

        Returns:
            CohereRerankClient instance or None if config is not available
        """
        if not config or not config.is_available():
            return None
        return cls(
            api_key=config.api_key,
            model=config.model_name if config.model_name != "doubao-seed-rerank" else "rerank-v3.5",
        )
