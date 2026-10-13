# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A rerank provider must never invent a score it was not given.

Many rerank services return only their ``top_n`` results. Filling the rest with
0.0 looks like a score but is a fabrication: ``score > threshold`` is false for
every one of them, so the caller prunes documents the reranker never judged.
Providers therefore report ``None`` for a document the service left unscored,
and ask for a score for every document in the first place.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from openviking.models.rerank.cohere_rerank import CohereRerankClient
from openviking.models.rerank.errors import RerankError
from openviking.models.rerank.openai_rerank import OpenAIRerankClient


def _openai_client(api_base="https://dashscope.aliyuncs.com/compatible-api/v1/reranks"):
    return OpenAIRerankClient(api_key="sk-test", api_base=api_base, model_name="qwen3-rerank")


def _openai_response(payload):
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def _cohere_client():
    with patch("openviking.models.rerank.cohere_rerank.httpx.Client"):
        return CohereRerankClient(api_key="test-key")


def _cohere_response(client, payload):
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    client._client.post.return_value = response
    return response


class TestUnscoredDocumentsAreNotFabricated:
    def test_openai_reports_none_for_documents_the_service_skipped(self):
        client = _openai_client()
        response = _openai_response({"results": [{"index": 1, "relevance_score": 0.55}]})

        with patch("openviking.models.rerank.openai_rerank.requests.post", return_value=response):
            scores = client.rerank_batch("hello", ["doc0", "doc1", "doc2"])

        assert scores == [None, 0.55, None]

    def test_cohere_reports_none_for_documents_the_service_skipped(self):
        client = _cohere_client()
        _cohere_response(client, {"results": [{"index": 2, "relevance_score": 0.9}]})

        scores = client.rerank_batch("q", ["first", "second", "third"])

        assert scores == [None, None, 0.9]

    def test_a_zero_score_from_the_service_is_kept_as_zero(self):
        # 0.0 must stay distinguishable from "not scored".
        client = _openai_client()
        response = _openai_response(
            {
                "results": [
                    {"index": 0, "relevance_score": 0.0},
                    {"index": 1, "relevance_score": 0.4},
                ]
            }
        )

        with patch("openviking.models.rerank.openai_rerank.requests.post", return_value=response):
            scores = client.rerank_batch("hello", ["doc0", "doc1"])

        assert scores == [0.0, 0.4]


class TestEveryDocumentIsAskedFor:
    """Sparse responses are usually self-inflicted: the service defaults to a
    small ``top_n`` because the request did not ask for more."""

    def test_openai_flat_request_asks_for_every_document(self):
        client = _openai_client()
        response = _openai_response({"results": [{"index": 0, "relevance_score": 0.5}]})

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post", return_value=response
        ) as post:
            client.rerank_batch("hello", ["doc0", "doc1", "doc2"])

        assert post.call_args.kwargs["json"]["top_n"] == 3

    def test_openai_nested_request_asks_for_every_document(self):
        client = _openai_client("https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank")
        response = _openai_response({"output": {"results": [{"index": 0, "relevance_score": 0.5}]}})

        with patch(
            "openviking.models.rerank.openai_rerank.requests.post", return_value=response
        ) as post:
            client.rerank_batch("hello", ["doc0", "doc1"])

        assert post.call_args.kwargs["json"]["parameters"]["top_n"] == 2


class TestCohereRejectsMalformedItems:
    """Cohere indexed results with bare subscripts: ``scores[result["index"]]``.

    A negative index is the dangerous case — it silently writes to a document
    counted from the end instead of raising, so a malformed response quietly
    scores the wrong document.
    """

    def test_negative_index_does_not_score_the_wrong_document(self):
        client = _cohere_client()
        _cohere_response(client, {"results": [{"index": -1, "relevance_score": 0.9}]})

        with pytest.raises(RerankError):
            client.rerank_batch("q", ["first", "second", "third"])

    @pytest.mark.parametrize(
        "item",
        [
            {"relevance_score": 0.9},  # no index
            {"index": 7, "relevance_score": 0.9},  # out of range
            {"index": "1", "relevance_score": 0.9},  # not an integer
            {"index": True, "relevance_score": 0.9},  # bool is not an index
            {"index": 0},  # no score
        ],
        ids=["missing-index", "out-of-range", "string-index", "bool-index", "missing-score"],
    )
    def test_malformed_item_is_rejected(self, item):
        client = _cohere_client()
        _cohere_response(client, {"results": [item]})

        with pytest.raises(RerankError):
            client.rerank_batch("q", ["first", "second"])

    def test_a_transport_error_is_reported(self):
        client = _cohere_client()
        client._client.post.side_effect = httpx.ConnectError("boom")

        with pytest.raises(RerankError):
            client.rerank_batch("q", ["first"])
