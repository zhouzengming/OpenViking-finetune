# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Documents the reranker did not score keep their vector score.

Providers report ``None`` for a document a rerank service left out of its
response. Downgrading just those documents — instead of discarding the whole
batch — keeps the rerank ordering that was actually computed. The downgrade is
invisible in the results, so it has to be counted.
"""

import pytest

from openviking.telemetry.context import bind_telemetry
from openviking.telemetry.operation import OperationTelemetry


class _Client:
    provider = "fake"

    def __init__(self, scores):
        self._scores = scores
        self.calls = []

    def rerank_batch(self, query, documents):
        self.calls.append((query, list(documents)))
        return self._scores


@pytest.mark.asyncio
async def test_unscored_documents_keep_their_vector_score(make_rerank_retriever):
    retriever = make_rerank_retriever(_Client([0.9, None, 0.2]))

    scores = await retriever._rerank_scores("q", ["a", "b", "c"], [0.31, 0.42, 0.53])

    assert scores == [0.9, 0.42, 0.2]


@pytest.mark.asyncio
async def test_a_zero_rerank_score_is_applied_not_treated_as_unscored(make_rerank_retriever):
    retriever = make_rerank_retriever(_Client([0.0, 0.7]))

    scores = await retriever._rerank_scores("q", ["a", "b"], [0.31, 0.42])

    assert scores == [0.0, 0.7]


@pytest.mark.asyncio
async def test_unscored_documents_are_counted(make_rerank_retriever):
    retriever = make_rerank_retriever(_Client([0.9, None, None]))
    telemetry = OperationTelemetry("search.find", enabled=True)

    with bind_telemetry(telemetry):
        await retriever._rerank_scores("q", ["a", "b", "c"], [0.1, 0.2, 0.3])

    assert telemetry.finish().summary["rerank"]["unscored_docs"] == 2


@pytest.mark.asyncio
async def test_a_fully_scored_batch_counts_no_unscored_documents(make_rerank_retriever):
    retriever = make_rerank_retriever(_Client([0.9, 0.8]))
    telemetry = OperationTelemetry("search.find", enabled=True)

    with bind_telemetry(telemetry):
        await retriever._rerank_scores("q", ["a", "b"], [0.1, 0.2])

    summary = telemetry.finish().summary
    assert summary["rerank"]["unscored_docs"] == 0
    assert summary["rerank"]["documents"] == 2


@pytest.mark.asyncio
async def test_blank_documents_are_not_counted_as_unscored(make_rerank_retriever):
    # Blank documents are never sent to the provider, so they are not a
    # rerank downgrade — they simply keep their vector score.
    retriever = make_rerank_retriever(_Client([0.9]))
    telemetry = OperationTelemetry("search.find", enabled=True)

    with bind_telemetry(telemetry):
        scores = await retriever._rerank_scores("q", ["a", "   "], [0.1, 0.2])

    assert scores == [0.9, 0.2]
    assert telemetry.finish().summary["rerank"]["unscored_docs"] == 0
