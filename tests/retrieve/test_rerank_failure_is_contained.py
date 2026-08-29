# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A rerank failure degrades the ranking; it never fails the retrieval.

Rerank is an optional improvement on top of vector scores. Whatever a provider
raises — rate limiting, a dead credential, a malformed response — the retrieval
still answers, using the scores it already had. The new typed error only makes
the reason visible; it must not become a new way for a search to fail.
"""

import logging

import pytest

from openviking.models.rerank.errors import RerankError
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking.utils.model_retry import ERROR_CLASS_TRANSIENT


class _Failing:
    def __init__(self, error):
        self._error = error

    def rerank_batch(self, query, documents):
        raise self._error


def _retriever(error):
    retriever = HierarchicalRetriever.__new__(HierarchicalRetriever)
    retriever._rerank_client = _Failing(error)
    retriever.rerank_max_input_tokens = 0
    retriever.rerank_on_failure = "fallback"
    return retriever


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        RerankError(
            "rate limited", provider="openai", error_class=ERROR_CLASS_TRANSIENT, status_code=429
        ),
        RuntimeError("something nobody anticipated"),
    ],
    ids=["typed", "untyped"],
)
async def test_the_vector_scores_survive_any_provider_failure(error):
    retriever = _retriever(error)

    scores = await retriever._rerank_scores("q", ["a", "b"], [0.31, 0.42])

    assert scores == [0.31, 0.42]


@pytest.mark.asyncio
async def test_the_error_class_reaches_the_log():
    # Without the class in the log, a rate limit and a dead credential look
    # identical to whoever is reading it.
    retriever = _retriever(
        RerankError("quota", provider="openai", error_class=ERROR_CLASS_TRANSIENT, status_code=429)
    )
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    logger = logging.getLogger("openviking.retrieve.hierarchical_retriever")
    logger.addHandler(handler)
    try:
        await retriever._rerank_scores("q", ["a"], [0.5])
    finally:
        logger.removeHandler(handler)

    messages = [record.getMessage() for record in records]
    assert any(ERROR_CLASS_TRANSIENT in message for message in messages), messages
