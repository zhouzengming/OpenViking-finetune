# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Shared harness for the rerank tests that exercise ``_rerank_scores`` directly."""

import pytest

from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking_cli.utils.config import RerankConfig


@pytest.fixture
def make_rerank_retriever():
    """Build a retriever wired to ``client``, skipping storage and embedding.

    The rerank settings come from ``RerankConfig`` defaults so a newly added
    setting cannot go missing here — a missing attribute would surface as an
    AttributeError swallowed by the fallback path, which reads exactly like a
    provider failure.
    """

    def _build(client, **overrides):
        config = RerankConfig(**overrides)
        retriever = HierarchicalRetriever.__new__(HierarchicalRetriever)
        retriever._rerank_client = client
        retriever.rerank_max_input_tokens = config.max_input_tokens
        retriever.rerank_on_failure = config.on_failure
        retriever.rerank_max_concurrency = config.max_concurrency
        return retriever

    return _build
