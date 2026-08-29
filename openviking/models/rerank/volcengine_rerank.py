# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""
VikingDB Rerank API Client.

Provides rerank functionality for hierarchical retrieval.
"""

import json

# For logging, use Python's built-in logging
import time
from typing import List, Optional

import requests
from volcengine.auth.SignerV4 import SignerV4
from volcengine.base.Request import Request
from volcengine.Credentials import Credentials

from openviking.models.rerank.base import RerankBase
from openviking.models.rerank.errors import RerankError, rerank_error_from_exception
from openviking.utils.model_retry import ERROR_CLASS_PERMANENT
from openviking_cli.utils import get_logger

logger = get_logger(__name__)


class RerankClient(RerankBase):
    """
    VikingDB Rerank API client.

    Supports batch rerank for multiple documents against a query.
    """

    def __init__(
        self,
        ak: str,
        sk: str,
        host: str = "api-vikingdb.vikingdb.cn-beijing.volces.com",
        model_name: str = "doubao-seed-rerank",
        model_version: str = "251028",
        max_retries: int = 0,
    ):
        """
        Initialize rerank client.

        Args:
            ak: VikingDB Access Key
            sk: VikingDB Secret Key
            host: VikingDB API host
            model_name: Rerank model name
            model_version: Rerank model version
            max_retries: Extra attempts after a retryable failure; 0 disables retrying.
        """
        super().__init__(max_retries=max_retries)
        self.ak = ak
        self.sk = sk
        self.host = host
        self.model_name = model_name
        self.model_version = model_version
        self.provider = "vikingdb"

    def _prepare_request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        data: Optional[dict] = None,
    ) -> Request:
        """Prepare signed request for VikingDB API."""
        r = Request()
        r.set_shema("https")
        r.set_method(method)
        r.set_connection_timeout(10)
        r.set_socket_timeout(30)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Host": self.host,
        }
        r.set_headers(headers)
        if params:
            r.set_query(params)
        r.set_host(self.host)
        r.set_path(path)
        if data is not None:
            r.set_body(json.dumps(data))
        credentials = Credentials(self.ak, self.sk, "vikingdb", "cn-beijing")
        SignerV4.sign(r, credentials)
        return r

    def _rerank_once(self, query: str, documents: List[str]) -> List[Optional[float]]:
        """One rerank call. Retrying is the base class's job."""
        # Build request body
        req_body = {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "data": [[{"text": doc}] for doc in documents],
            "query": [{"text": query}],
            "instruction": "Whether the Document answers the Query or matches the content retrieval intent",
        }

        try:
            started = time.monotonic()
            req = self._prepare_request(
                method="POST",
                path="/api/vikingdb/rerank",
                data=req_body,
            )

            response = requests.request(
                method=req.method,
                url=f"https://{self.host}{req.path}",
                headers=req.headers,
                data=req.body,
                timeout=30,
            )

            try:
                result = response.json()
            except Exception as e:
                raise RerankError(
                    f"Non-JSON response: status={response.status_code} body={response.text[:1000]}",
                    provider=self.provider,
                    error_class=ERROR_CLASS_PERMANENT,
                    status_code=response.status_code,
                ) from e

            if not isinstance(result, dict):
                raise RerankError(
                    f"Unexpected response type: status={response.status_code} "
                    f"type={type(result).__name__} body={str(result)[:1000]}",
                    provider=self.provider,
                    error_class=ERROR_CLASS_PERMANENT,
                    status_code=response.status_code,
                )

            result_payload = result.get("result")
            if not isinstance(result_payload, dict):
                raise RerankError(
                    f"Unexpected response payload: status={response.status_code} "
                    f"result_type={type(result_payload).__name__} body={str(result)[:1000]}",
                    provider=self.provider,
                    error_class=ERROR_CLASS_PERMANENT,
                    status_code=response.status_code,
                )

            if "data" not in result_payload:
                raise RerankError(
                    f"Missing rerank data field: status={response.status_code} "
                    f"body={str(result)[:1000]}",
                    provider=self.provider,
                    error_class=ERROR_CLASS_PERMANENT,
                    status_code=response.status_code,
                )

            # Update token usage tracking (estimate, VikingDB doesn't provide token info)
            self._extract_and_update_token_usage(
                result,
                query,
                documents,
                duration_seconds=time.monotonic() - started,
            )

            # Each document is a separate group. The API sorts groups by score
            # and tags each one with "id" = its index in the request, so scores
            # must be mapped back by id. Responses without "id" keep the legacy
            # positional reading.
            data = result_payload["data"]
            if len(data) != len(documents):
                raise RerankError(
                    f"Unexpected rerank result length: expected={len(documents)} "
                    f"actual={len(data)}",
                    provider=self.provider,
                    error_class=ERROR_CLASS_PERMANENT,
                )

            scores = [0.0] * len(documents)
            assigned: set[int] = set()
            for position, item in enumerate(data):
                index = item.get("id", position)
                if not isinstance(index, int) or isinstance(index, bool):
                    raise RerankError(
                        f"Non-integer id in rerank item: {item}",
                        provider=self.provider,
                        error_class=ERROR_CLASS_PERMANENT,
                    )
                if not (0 <= index < len(documents)):
                    raise RerankError(
                        f"Out-of-bounds id in rerank item: {item}",
                        provider=self.provider,
                        error_class=ERROR_CLASS_PERMANENT,
                    )
                if index in assigned:
                    raise RerankError(
                        f"Duplicate id in rerank result: {item}",
                        provider=self.provider,
                        error_class=ERROR_CLASS_PERMANENT,
                    )
                assigned.add(index)
                scores[index] = item.get("score", 0.0)

            logger.debug(f"[RerankClient] Reranked {len(documents)} documents")
            return scores

        except RerankError:
            raise
        except Exception as e:
            raise rerank_error_from_exception(e, provider=self.provider) from e

    @classmethod
    def from_config(cls, config) -> Optional["RerankClient"]:
        """
        Create RerankClient from RerankConfig.

        Args:
            config: RerankConfig instance

        Returns:
            RerankClient instance or None if config is not available
        """
        if not config or not config.is_available():
            return None

        provider = config._effective_provider()

        if provider == "cohere":
            from openviking.models.rerank.cohere_rerank import CohereRerankClient

            return CohereRerankClient.from_config(config)

        if provider == "litellm":
            from openviking.models.rerank.litellm_rerank import LiteLLMRerankClient

            return LiteLLMRerankClient.from_config(config)

        if provider == "openai":
            from openviking.models.rerank.openai_rerank import OpenAIRerankClient

            return OpenAIRerankClient.from_config(config)

        return cls(
            ak=config.ak,
            sk=config.sk,
            host=config.host,
            model_name=config.model_name,
            model_version=config.model_version,
            max_retries=config.max_retries,
        )
