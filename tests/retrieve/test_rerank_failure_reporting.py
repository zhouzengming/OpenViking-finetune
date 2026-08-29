# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A rerank that silently stopped working must still be visible.

Falling back to vector scores returns HTTP 200 with a plausible-looking result,
so a provider that is throttling every call degrades retrieval quality with no
error, no alert, and nothing to count. Under provider TPM limits this was
measured as a 1:1 ratio of 429s to fallbacks, invisible from the outside.

Counting is the default answer. Refusing to serve a degraded result is
available for deployments that would rather fail than quietly rank worse.
"""

import pytest

from openviking.models.rerank.errors import RerankError
from openviking.telemetry.context import bind_telemetry
from openviking.telemetry.operation import OperationTelemetry
from openviking.utils.model_retry import (
    ERROR_CLASS_AUTH,
    ERROR_CLASS_PERMANENT,
    ERROR_CLASS_TRANSIENT,
)
from openviking_cli.exceptions import ResourceExhaustedError, UnavailableError
from openviking_cli.utils.config import RerankConfig


class _Client:
    def __init__(self, outcome):
        self._outcome = outcome

    def rerank_batch(self, query, documents):
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


async def _run(make_rerank_retriever, outcome, on_failure="fallback"):
    retriever = make_rerank_retriever(_Client(outcome), on_failure=on_failure)
    telemetry = OperationTelemetry("search.find", enabled=True)
    with bind_telemetry(telemetry):
        scores = await retriever._rerank_scores("q", ["a", "b"], [0.31, 0.42])
    return scores, telemetry.finish().summary["rerank"]


class TestFailuresAreCounted:
    @pytest.mark.asyncio
    async def test_a_fallback_is_counted_and_classified(self, make_rerank_retriever):
        scores, rerank = await _run(
            make_rerank_retriever,
            RerankError("boom", provider="openai", error_class=ERROR_CLASS_TRANSIENT),
        )

        assert scores == [0.31, 0.42]
        assert rerank["fallbacks"] == 1
        assert rerank["failures"] == {ERROR_CLASS_TRANSIENT: 1}

    @pytest.mark.asyncio
    async def test_rate_limiting_is_broken_out_from_other_transient_failures(
        self, make_rerank_retriever
    ):
        # Both classify as transient, but throttling and a flaky upstream call
        # for different operational responses.
        _, throttled = await _run(
            make_rerank_retriever,
            RerankError(
                "429", provider="openai", error_class=ERROR_CLASS_TRANSIENT, status_code=429
            ),
        )
        _, flaky = await _run(
            make_rerank_retriever,
            RerankError(
                "503", provider="openai", error_class=ERROR_CLASS_TRANSIENT, status_code=503
            ),
        )

        assert throttled["rate_limited"] == 1
        assert flaky["rate_limited"] == 0

    @pytest.mark.asyncio
    async def test_a_credential_failure_is_classified_as_auth(self, make_rerank_retriever):
        _, rerank = await _run(
            make_rerank_retriever,
            RerankError("401", provider="openai", error_class=ERROR_CLASS_AUTH, status_code=401),
        )

        assert rerank["failures"] == {ERROR_CLASS_AUTH: 1}

    @pytest.mark.asyncio
    async def test_an_untyped_failure_is_still_counted(self, make_rerank_retriever):
        _, rerank = await _run(make_rerank_retriever, RuntimeError("nobody anticipated this"))

        assert rerank["fallbacks"] == 1
        assert sum(rerank["failures"].values()) == 1

    @pytest.mark.asyncio
    async def test_a_successful_rerank_counts_no_fallback(self, make_rerank_retriever):
        _, rerank = await _run(make_rerank_retriever, [0.9, 0.8])

        assert rerank["fallbacks"] == 0
        assert rerank["failures"] == {}
        assert rerank["rate_limited"] == 0


class TestStrictMode:
    def test_falling_back_is_the_default(self):
        assert RerankConfig().on_failure == "fallback"

    @pytest.mark.asyncio
    async def test_throttling_surfaces_as_resource_exhausted(self, make_rerank_retriever):
        with pytest.raises(ResourceExhaustedError):
            await _run(
                make_rerank_retriever,
                RerankError(
                    "429", provider="openai", error_class=ERROR_CLASS_TRANSIENT, status_code=429
                ),
                on_failure="error",
            )

    @pytest.mark.asyncio
    async def test_other_failures_surface_as_unavailable(self, make_rerank_retriever):
        with pytest.raises(UnavailableError):
            await _run(
                make_rerank_retriever,
                RerankError("bad key", provider="openai", error_class=ERROR_CLASS_AUTH),
                on_failure="error",
            )

    @pytest.mark.asyncio
    async def test_a_malformed_result_also_refuses_in_strict_mode(self, make_rerank_retriever):
        with pytest.raises(UnavailableError):
            await _run(
                make_rerank_retriever,
                RerankError("garbage", provider="openai", error_class=ERROR_CLASS_PERMANENT),
                on_failure="error",
            )

    @pytest.mark.asyncio
    async def test_strict_mode_returns_normally_when_rerank_works(self, make_rerank_retriever):
        scores, _ = await _run(make_rerank_retriever, [0.9, 0.8], on_failure="error")

        assert scores == [0.9, 0.8]

    @pytest.mark.asyncio
    async def test_unscored_documents_are_not_a_failure_even_in_strict_mode(
        self, make_rerank_retriever
    ):
        # A partial ranking is still a ranking; only a failed call is a failure.
        scores, _ = await _run(make_rerank_retriever, [0.9, None], on_failure="error")

        assert scores == [0.9, 0.42]
