# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Rerank gets its own threads instead of competing for the shared default pool.

``asyncio.to_thread`` uses the interpreter's default executor — ``min(32, cpu+4)``
slots shared by every blocking call in the process: git, storage, crypto, OAuth,
the vector backend. Rerank both starved them and was starved by them, with a
ceiling nobody chose and no way to tune it.

A dedicated pool is process-wide on purpose: a retriever is built per search, so
a per-instance pool would create and discard threads on every request.
"""

import asyncio
import threading

import pytest

from openviking.models.rerank.executor import (
    default_rerank_concurrency,
    get_rerank_executor,
    shutdown_rerank_executor,
)
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking.telemetry.context import bind_telemetry
from openviking.telemetry.operation import OperationTelemetry
from openviking_cli.utils.config import RerankConfig


@pytest.fixture(autouse=True)
def _fresh_executor():
    shutdown_rerank_executor()
    yield
    shutdown_rerank_executor()


class _Client:
    provider = "fake"

    def __init__(self, on_call=None):
        self.threads = []
        self._on_call = on_call

    def rerank_batch(self, query, documents):
        self.threads.append(threading.current_thread().name)
        if self._on_call:
            self._on_call()
        return [0.9] * len(documents)


def _retriever(client, max_concurrency=0):
    retriever = HierarchicalRetriever.__new__(HierarchicalRetriever)
    retriever._rerank_client = client
    retriever.rerank_max_input_tokens = 0
    retriever.rerank_on_failure = "fallback"
    retriever.rerank_max_concurrency = max_concurrency
    return retriever


class TestDedicatedPool:
    @pytest.mark.asyncio
    async def test_rerank_runs_on_a_rerank_thread(self):
        client = _Client()

        await _retriever(client)._rerank_scores("q", ["a"], [0.1])

        assert client.threads
        assert all(name.startswith("ov-rerank") for name in client.threads), client.threads

    def test_the_default_size_matches_the_pool_it_replaces(self):
        # Nothing should get slower by default: same ceiling, just not shared.
        import os

        assert default_rerank_concurrency() == min(32, (os.cpu_count() or 1) + 4)

    def test_the_pool_is_created_once(self):
        assert get_rerank_executor(4) is get_rerank_executor(4)

    def test_a_later_different_size_does_not_silently_resize(self):
        first = get_rerank_executor(2)

        assert get_rerank_executor(9) is first
        assert first._max_workers == 2


class TestConcurrencyIsBounded:
    @pytest.mark.asyncio
    async def test_no_more_calls_run_at_once_than_configured(self):
        active = 0
        peak = 0
        lock = threading.Lock()
        release = threading.Event()

        def on_call():
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            release.wait(2)
            with lock:
                active -= 1

        retriever = _retriever(_Client(on_call), max_concurrency=2)
        tasks = [asyncio.create_task(retriever._rerank_scores("q", ["a"], [0.1])) for _ in range(6)]
        await asyncio.sleep(0.3)
        release.set()
        await asyncio.gather(*tasks)

        assert peak == 2, peak


class TestTelemetryStillReachesTheWorkerThread:
    @pytest.mark.asyncio
    async def test_counters_recorded_inside_the_pool_are_kept(self):
        # run_in_executor does not copy contextvars the way to_thread does, so
        # the telemetry bound to the request would be lost without help.
        recorded = []

        def on_call():
            from openviking.telemetry import get_current_telemetry

            get_current_telemetry().count("rerank.retries", 1)
            recorded.append(True)

        telemetry = OperationTelemetry("search.find", enabled=True)
        with bind_telemetry(telemetry):
            await _retriever(_Client(on_call))._rerank_scores("q", ["a"], [0.1])

        assert recorded
        assert telemetry.finish().summary["rerank"]["retries"] == 1


class TestConfiguration:
    def test_unset_means_the_default_size(self):
        assert RerankConfig().max_concurrency == 0
