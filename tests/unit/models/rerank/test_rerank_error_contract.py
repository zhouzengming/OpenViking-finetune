# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A failed rerank must say why it failed.

Every provider used to collapse rate limiting, credential failure, timeouts and
malformed responses into the same ``return None``. The caller could therefore
only fall back — never retry what is worth retrying, never report what is worth
reporting. Providers now raise ``RerankError`` carrying one of ``model_retry``'s
error classes; falling back stays the caller's decision, unchanged.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest
import requests

from openviking.models.rerank.cohere_rerank import CohereRerankClient
from openviking.models.rerank.errors import RerankError
from openviking.models.rerank.litellm_rerank import LiteLLMRerankClient
from openviking.models.rerank.openai_rerank import OpenAIRerankClient
from openviking.models.rerank.volcengine_rerank import RerankClient
from openviking.utils.model_retry import (
    ERROR_CLASS_AUTH,
    ERROR_CLASS_PERMANENT,
    ERROR_CLASS_TRANSIENT,
)


def _http_error(status_code, reason="Boom"):
    response = requests.Response()
    response.status_code = status_code
    response.reason = reason
    response.url = "https://example.test/rerank"
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        return e
    raise AssertionError("status did not raise")


def _openai_client():
    return OpenAIRerankClient(
        api_key="sk-test",
        api_base="https://dashscope.aliyuncs.com/compatible-api/v1/reranks",
        model_name="qwen3-rerank",
    )


def _openai_json(payload):
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def _cohere_client():
    with patch("openviking.models.rerank.cohere_rerank.httpx.Client"):
        return CohereRerankClient(api_key="test-key")


def _volcengine_client():
    return RerankClient(ak="ak", sk="sk")


class TestHttpStatusIsClassified:
    @pytest.mark.parametrize(
        "status,expected",
        [
            (429, ERROR_CLASS_TRANSIENT),
            (503, ERROR_CLASS_TRANSIENT),
            (401, ERROR_CLASS_AUTH),
            (403, ERROR_CLASS_AUTH),
            (400, ERROR_CLASS_PERMANENT),
        ],
    )
    def test_openai_reports_the_error_class(self, status, expected):
        client = _openai_client()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post",
            side_effect=_http_error(status),
        ):
            with pytest.raises(RerankError) as excinfo:
                client.rerank_batch("q", ["doc"])

        assert excinfo.value.error_class == expected
        assert excinfo.value.status_code == status
        assert excinfo.value.provider == "openai"

    def test_the_original_exception_stays_reachable(self):
        client = _openai_client()
        original = _http_error(429)

        with patch("openviking.models.rerank.openai_rerank.requests.post", side_effect=original):
            with pytest.raises(RerankError) as excinfo:
                client.rerank_batch("q", ["doc"])

        assert excinfo.value.__cause__ is original

    def test_a_timeout_is_transient(self):
        client = _openai_client()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post",
            side_effect=requests.exceptions.ReadTimeout(
                "HTTPSConnectionPool(host='x', port=443): Read timed out. (read timeout=60)"
            ),
        ):
            with pytest.raises(RerankError) as excinfo:
                client.rerank_batch("q", ["doc"])

        assert excinfo.value.error_class == ERROR_CLASS_TRANSIENT

    def test_cohere_reports_the_error_class(self):
        client = _cohere_client()
        response = httpx.Response(429, request=httpx.Request("POST", "https://example.test"))
        client._client.post.side_effect = httpx.HTTPStatusError(
            "rate limited", request=response.request, response=response
        )

        with pytest.raises(RerankError) as excinfo:
            client.rerank_batch("q", ["doc"])

        assert excinfo.value.error_class == ERROR_CLASS_TRANSIENT
        assert excinfo.value.status_code == 429
        assert excinfo.value.provider == "cohere"

    def test_volcengine_reports_the_error_class(self):
        client = _volcengine_client()

        with patch(
            "openviking.models.rerank.volcengine_rerank.requests.request",
            side_effect=_http_error(429),
        ):
            with pytest.raises(RerankError) as excinfo:
                client.rerank_batch("q", ["doc"])

        assert excinfo.value.error_class == ERROR_CLASS_TRANSIENT
        assert excinfo.value.provider == "vikingdb"

    def test_litellm_reports_the_error_class(self):
        client = LiteLLMRerankClient(api_key="k", api_base=None, model_name="cohere/rerank-v3.5")

        with patch("litellm.rerank", side_effect=_http_error(401)):
            with pytest.raises(RerankError) as excinfo:
                client.rerank_batch("q", ["doc"])

        assert excinfo.value.error_class == ERROR_CLASS_AUTH
        assert excinfo.value.provider == "litellm"


class TestMalformedResponsesArePermanent:
    """Retrying a response the service is happy with cannot help."""

    def test_openai_unexpected_payload(self):
        client = _openai_client()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post",
            return_value=_openai_json({"nonsense": True}),
        ):
            with pytest.raises(RerankError) as excinfo:
                client.rerank_batch("q", ["doc"])

        assert excinfo.value.error_class == ERROR_CLASS_PERMANENT

    def test_openai_out_of_bounds_index(self):
        client = _openai_client()

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post",
            return_value=_openai_json({"results": [{"index": 9, "relevance_score": 0.5}]}),
        ):
            with pytest.raises(RerankError) as excinfo:
                client.rerank_batch("q", ["doc"])

        assert excinfo.value.error_class == ERROR_CLASS_PERMANENT

    def test_cohere_malformed_item(self):
        client = _cohere_client()
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"results": [{"index": -1, "relevance_score": 0.5}]}
        client._client.post.return_value = response

        with pytest.raises(RerankError) as excinfo:
            client.rerank_batch("q", ["doc"])

        assert excinfo.value.error_class == ERROR_CLASS_PERMANENT

    def test_volcengine_wrong_result_length(self):
        client = _volcengine_client()
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"result": {"data": [{"id": 0, "score": 0.5}]}}

        with patch(
            "openviking.models.rerank.volcengine_rerank.requests.request", return_value=response
        ):
            with pytest.raises(RerankError) as excinfo:
                client.rerank_batch("q", ["doc-a", "doc-b"])

        assert excinfo.value.error_class == ERROR_CLASS_PERMANENT

    def test_litellm_wrong_result_length(self):
        client = LiteLLMRerankClient(api_key="k", api_base=None, model_name="cohere/rerank-v3.5")
        response = MagicMock()
        response.results = [{"index": 0, "relevance_score": 0.5}]
        response.model_dump.return_value = {}

        with patch("litellm.rerank", return_value=response):
            with pytest.raises(RerankError) as excinfo:
                client.rerank_batch("q", ["doc-a", "doc-b"])

        assert excinfo.value.error_class == ERROR_CLASS_PERMANENT


class TestNoDocumentsIsNotAFailure:
    @pytest.mark.parametrize(
        "client_factory",
        [_openai_client, _cohere_client, _volcengine_client],
        ids=["openai", "cohere", "vikingdb"],
    )
    def test_empty_input_returns_empty_scores(self, client_factory):
        assert client_factory().rerank_batch("q", []) == []
