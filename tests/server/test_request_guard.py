# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Requests must stop when the client goes away or the deadline passes.

Starlette never cancels a handler on client disconnect, and uvicorn is started
without a request timeout, so without this guard an abandoned retrieval keeps a
GPU busy until it finishes on its own.
"""

import asyncio

import pytest

from openviking.server.models import ERROR_CODE_TO_HTTP_STATUS
from openviking.server.request_guard import run_guarded
from openviking_cli.exceptions import ClientDisconnectedError, DeadlineExceededError


class FakeRequest:
    """Stands in for a Starlette Request, counting disconnect polls."""

    def __init__(self, disconnect_after_polls=None):
        self.polls = 0
        self._disconnect_after_polls = disconnect_after_polls

    async def is_disconnected(self) -> bool:
        self.polls += 1
        return (
            self._disconnect_after_polls is not None and self.polls >= self._disconnect_after_polls
        )


def _slow_work(cleanup_flag, seconds=30):
    async def work():
        try:
            await asyncio.sleep(seconds)
            return "finished"
        finally:
            cleanup_flag.append("cleaned")

    return work


class TestClientDisconnect:
    async def test_returns_result_while_client_stays_connected(self):
        async def work():
            return "done"

        result = await run_guarded(
            work, operation="search.find", http_request=FakeRequest(), poll_seconds=0.01
        )

        assert result == "done"

    async def test_disconnect_aborts_the_work(self):
        cleanup = []

        with pytest.raises(ClientDisconnectedError):
            await run_guarded(
                _slow_work(cleanup),
                operation="search.find",
                http_request=FakeRequest(disconnect_after_polls=2),
                poll_seconds=0.01,
            )

        # The work must be cancelled, and cancellation must be awaited so its
        # finally blocks release locks and telemetry before we return.
        assert cleanup == ["cleaned"]

    async def test_original_exception_is_not_rewritten(self):
        async def work():
            raise ValueError("upstream blew up")

        with pytest.raises(ValueError, match="upstream blew up"):
            await run_guarded(
                work, operation="search.find", http_request=FakeRequest(), poll_seconds=0.01
            )

    async def test_no_tasks_are_leaked(self):
        before = len(asyncio.all_tasks())

        async def work():
            return "done"

        await run_guarded(
            work, operation="search.find", http_request=FakeRequest(), poll_seconds=0.01
        )
        await asyncio.sleep(0)

        assert len(asyncio.all_tasks()) == before


class TestDeadline:
    async def test_deadline_aborts_the_work(self):
        cleanup = []

        with pytest.raises(DeadlineExceededError) as excinfo:
            await run_guarded(_slow_work(cleanup), operation="search.find", timeout=0.05)

        assert cleanup == ["cleaned"]
        assert excinfo.value.details["operation"] == "search.find"
        assert excinfo.value.details["timeout"] == 0.05

    async def test_zero_timeout_means_no_deadline(self):
        async def work():
            await asyncio.sleep(0.05)
            return "done"

        assert await run_guarded(work, operation="search.find", timeout=0) == "done"

    async def test_disconnect_and_deadline_compose(self):
        # Both guards active: the client leaves well before the deadline.
        cleanup = []

        with pytest.raises(ClientDisconnectedError):
            await run_guarded(
                _slow_work(cleanup),
                operation="search.find",
                http_request=FakeRequest(disconnect_after_polls=2),
                timeout=30,
                poll_seconds=0.01,
            )

        assert cleanup == ["cleaned"]


class TestErrorEnvelope:
    def test_cancelled_maps_to_499(self):
        assert ERROR_CODE_TO_HTTP_STATUS["CANCELLED"] == 499

    def test_client_disconnected_error_uses_cancelled_code(self):
        assert ClientDisconnectedError("search.find").code == "CANCELLED"


class TestRunOperationWiring:
    """The guard must be reachable from the routers, not just importable."""

    @pytest.fixture(autouse=True)
    def _restore_server_config(self):
        from openviking.server.dependencies import get_server_config, set_server_config

        previous = get_server_config()
        yield
        if previous is not None:
            set_server_config(previous)

    async def test_run_operation_aborts_when_client_disconnects(self):
        from openviking.server.telemetry import run_operation

        cleanup = []

        with pytest.raises(ClientDisconnectedError):
            await run_operation(
                operation="search.find",
                telemetry=False,
                fn=_slow_work(cleanup),
                http_request=FakeRequest(disconnect_after_polls=1),
            )

        assert cleanup == ["cleaned"]

    async def test_run_operation_applies_the_configured_deadline(self):
        from openviking.server.config import ServerConfig
        from openviking.server.dependencies import set_server_config
        from openviking.server.telemetry import run_operation

        set_server_config(ServerConfig(request_timeout_seconds=0.05))
        cleanup = []

        with pytest.raises(DeadlineExceededError):
            await run_operation(operation="search.find", telemetry=False, fn=_slow_work(cleanup))

        assert cleanup == ["cleaned"]

    def test_request_timeout_is_disabled_by_default(self):
        from openviking.server.config import ServerConfig

        assert ServerConfig().request_timeout_seconds == 0.0


class TestSearchRoutesAreGuarded:
    """The retrieval endpoints are the ones that pin a GPU when abandoned."""

    @pytest.mark.parametrize("endpoint", ["find", "search", "recall", "grep", "glob"])
    def test_endpoint_takes_the_http_request(self, endpoint):
        import inspect

        from starlette.requests import Request

        import openviking.server.routers.search as search_router

        annotations = [
            parameter.annotation
            for parameter in inspect.signature(getattr(search_router, endpoint)).parameters.values()
        ]

        assert Request in annotations

    def test_guard_parameter_stays_out_of_the_api_schema(self):
        # FastAPI injects Request by annotation, but a rename or a wrong
        # annotation would silently turn it into a query parameter and change
        # the public API.
        from fastapi import FastAPI

        from openviking.server.routers.search import router

        app = FastAPI()
        app.include_router(router)
        spec = app.openapi()

        expected_bodies = {
            "/api/v1/search/find": "FindRequest",
            "/api/v1/search/search": "SearchRequest",
            "/api/v1/search/recall": "RecallRequest",
            "/api/v1/search/grep": "GrepRequest",
            "/api/v1/search/glob": "GlobRequest",
        }
        for path, expected in expected_bodies.items():
            operation = spec["paths"][path]["post"]
            schema = operation["requestBody"]["content"]["application/json"]["schema"]
            assert schema["$ref"].rsplit("/", 1)[-1] == expected
            names = {parameter["name"] for parameter in operation.get("parameters", [])}
            assert "http_request" not in names
