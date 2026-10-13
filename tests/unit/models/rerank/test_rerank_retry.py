# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Rate limiting is worth waiting out; a bad key is not.

Under provider TPM limits, 429s and silent vector-score fallbacks were observed
one-for-one: every throttled call quietly dropped the reranking for that batch.
Nothing retried, because nothing could tell a 429 from a 401.

Retrying is off by default — enabling it trades latency inside a live search for
ranking quality, which is the operator's call.
"""

from unittest.mock import patch

import pytest

from openviking.models.rerank.base import RerankBase
from openviking.models.rerank.errors import RerankError
from openviking.telemetry.context import bind_telemetry
from openviking.telemetry.operation import OperationTelemetry
from openviking.utils.model_retry import (
    ERROR_CLASS_AUTH,
    ERROR_CLASS_CONTENT_SAFETY,
    ERROR_CLASS_INPUT_TOO_LARGE,
    ERROR_CLASS_PERMANENT,
    ERROR_CLASS_QUOTA_EXCEEDED,
    ERROR_CLASS_TRANSIENT,
    ERROR_CLASS_UNKNOWN,
)
from openviking_cli.utils.config import RerankConfig


class _Provider(RerankBase):
    """A provider whose every attempt is scripted."""

    def __init__(self, attempts, **kwargs):
        super().__init__(**kwargs)
        self.provider = "fake"
        self._attempts = list(attempts)
        self.calls = 0

    def _rerank_once(self, query, documents):
        self.calls += 1
        outcome = self._attempts.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _error(error_class, **kwargs):
    return RerankError("boom", provider="fake", error_class=error_class, **kwargs)


@pytest.fixture(autouse=True)
def _no_real_sleeping():
    with patch("openviking.models.rerank.base.time.sleep") as sleep:
        yield sleep


class TestRetryIsOffByDefault:
    def test_config_default(self):
        assert RerankConfig().max_retries == 0

    def test_a_transient_failure_is_raised_without_a_second_attempt(self):
        provider = _Provider([_error(ERROR_CLASS_TRANSIENT)])

        with pytest.raises(RerankError):
            provider.rerank_batch("q", ["doc"])

        assert provider.calls == 1


class TestWhatGetsRetried:
    @pytest.mark.parametrize("error_class", [ERROR_CLASS_TRANSIENT, ERROR_CLASS_QUOTA_EXCEEDED])
    def test_throttling_and_outages_are_retried(self, error_class):
        provider = _Provider([_error(error_class), _error(error_class), [0.5]], max_retries=2)

        assert provider.rerank_batch("q", ["doc"]) == [0.5]
        assert provider.calls == 3

    @pytest.mark.parametrize(
        "error_class",
        [
            ERROR_CLASS_AUTH,
            ERROR_CLASS_PERMANENT,
            ERROR_CLASS_CONTENT_SAFETY,
            ERROR_CLASS_INPUT_TOO_LARGE,
            ERROR_CLASS_UNKNOWN,
        ],
    )
    def test_failures_that_will_repeat_are_not_retried(self, error_class):
        provider = _Provider([_error(error_class)], max_retries=3)

        with pytest.raises(RerankError):
            provider.rerank_batch("q", ["doc"])

        assert provider.calls == 1

    def test_the_last_failure_is_raised_once_the_budget_runs_out(self):
        provider = _Provider([_error(ERROR_CLASS_TRANSIENT)] * 3, max_retries=2)

        with pytest.raises(RerankError) as excinfo:
            provider.rerank_batch("q", ["doc"])

        assert provider.calls == 3
        assert excinfo.value.error_class == ERROR_CLASS_TRANSIENT


class TestBackoff:
    def test_waits_grow_and_stay_capped(self, _no_real_sleeping):
        from openviking.models.rerank.base import (
            RERANK_RETRY_BASE_DELAY_SECONDS,
            RERANK_RETRY_MAX_DELAY_SECONDS,
        )

        provider = _Provider([_error(ERROR_CLASS_TRANSIENT)] * 9, max_retries=8)

        # Pin the jitter so the growth curve itself is what is asserted.
        with patch("openviking.models.rerank.base.random.uniform", return_value=1.0):
            with pytest.raises(RerankError):
                provider.rerank_batch("q", ["doc"])

        waits = [call.args[0] for call in _no_real_sleeping.call_args_list]
        assert len(waits) == 8
        assert waits[0] == RERANK_RETRY_BASE_DELAY_SECONDS
        assert waits == sorted(waits)
        assert waits[-1] == RERANK_RETRY_MAX_DELAY_SECONDS

    def test_waits_are_jittered(self, _no_real_sleeping):
        # Identical backoff across concurrent searches would resynchronise them
        # into the next rate limit together.
        provider = _Provider([_error(ERROR_CLASS_TRANSIENT)] * 21, max_retries=20)

        with pytest.raises(RerankError):
            provider.rerank_batch("q", ["doc"])

        waits = [call.args[0] for call in _no_real_sleeping.call_args_list]
        capped = waits[-8:]  # long past the cap: without jitter these are equal
        assert len(set(capped)) > 1

    def test_a_retry_after_header_is_obeyed(self, _no_real_sleeping):
        provider = _Provider([_error(ERROR_CLASS_TRANSIENT, retry_after=3.0), [0.5]], max_retries=1)

        provider.rerank_batch("q", ["doc"])

        assert _no_real_sleeping.call_args_list[0].args[0] == 3.0

    def test_an_absurd_retry_after_is_capped(self, _no_real_sleeping):
        from openviking.models.rerank.base import RERANK_RETRY_MAX_DELAY_SECONDS

        provider = _Provider(
            [_error(ERROR_CLASS_TRANSIENT, retry_after=3600.0), [0.5]], max_retries=1
        )

        provider.rerank_batch("q", ["doc"])

        assert _no_real_sleeping.call_args_list[0].args[0] == RERANK_RETRY_MAX_DELAY_SECONDS


class TestRetriesAreVisible:
    def test_retries_are_counted(self):
        provider = _Provider(
            [_error(ERROR_CLASS_TRANSIENT), _error(ERROR_CLASS_TRANSIENT), [0.5]], max_retries=2
        )
        telemetry = OperationTelemetry("search.find", enabled=True)

        with bind_telemetry(telemetry):
            provider.rerank_batch("q", ["doc"])

        assert telemetry.finish().summary["rerank"]["retries"] == 2


class TestNoDocumentsNeverReachesTheProvider:
    def test_empty_input_short_circuits(self):
        provider = _Provider([], max_retries=3)

        assert provider.rerank_batch("q", []) == []
        assert provider.calls == 0
