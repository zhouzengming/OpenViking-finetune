# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for the VikingDB rerank client score mapping."""

from unittest.mock import MagicMock, patch

import pytest

from openviking.models.rerank.errors import RerankError
from openviking.models.rerank.volcengine_rerank import RerankClient


def _make_response(data):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"result": {"data": data}}
    return response


def _client():
    return RerankClient(ak="ak", sk="sk")


class TestVikingDBRerankScoreMapping:
    """The API returns items sorted by score, tagged with the input index."""

    @patch("openviking.models.rerank.volcengine_rerank.requests.request")
    def test_scores_map_back_by_id_not_position(self, mock_request):
        # VikingDB returns rerank items in descending score order and marks each
        # one with "id" = its index in the request. Reading scores positionally
        # silently pairs every score with the wrong document.
        mock_request.return_value = _make_response(
            [
                {"id": 0, "score": 0.992},
                {"id": 2, "score": 0.610},
                {"id": 1, "score": 0.065},
            ]
        )

        scores = _client().rerank_batch("q", ["first", "second", "third"])

        assert scores == [0.992, 0.065, 0.610]

    @patch("openviking.models.rerank.volcengine_rerank.requests.request")
    def test_missing_id_falls_back_to_position(self, mock_request):
        # Older responses carry no "id"; those must keep the positional reading.
        mock_request.return_value = _make_response([{"score": 0.9}, {"score": 0.8}, {"score": 0.7}])

        scores = _client().rerank_batch("q", ["first", "second", "third"])

        assert scores == [0.9, 0.8, 0.7]

    @patch("openviking.models.rerank.volcengine_rerank.requests.request")
    def test_out_of_range_id_is_reported(self, mock_request):
        # An unusable id must be reported so the caller falls back to vector
        # scores, rather than writing to a wrong slot.
        mock_request.return_value = _make_response(
            [{"id": 0, "score": 0.9}, {"id": 7, "score": 0.8}, {"id": 2, "score": 0.7}]
        )

        with pytest.raises(RerankError):
            _client().rerank_batch("q", ["first", "second", "third"])

    @patch("openviking.models.rerank.volcengine_rerank.requests.request")
    def test_duplicate_id_is_reported(self, mock_request):
        # Two items claiming the same slot leave one document unscored; a silent
        # 0.0 there would be pruned by the relevance threshold.
        mock_request.return_value = _make_response(
            [{"id": 0, "score": 0.9}, {"id": 0, "score": 0.8}, {"id": 2, "score": 0.7}]
        )

        with pytest.raises(RerankError):
            _client().rerank_batch("q", ["first", "second", "third"])
