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
    """Stands in for a Starlette Request, serving ASGI messages."""

    def __init__(self, disconnect_after_polls=None):
        self.polls = 0
        self._disconnect_after_polls = disconnect_after_polls

    async def receive(self):
        self.polls += 1
        if self._disconnect_after_polls is None:
            # A still-connected client sends nothing more; block like a real one.
            await asyncio.sleep(3600)
        if self.polls >= self._disconnect_after_polls:
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": b"", "more_body": False}


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

        result = await run_guarded(work, operation="search.find", http_request=FakeRequest())

        assert result == "done"

    async def test_disconnect_aborts_the_work(self):
        cleanup = []

        with pytest.raises(ClientDisconnectedError):
            await run_guarded(
                _slow_work(cleanup),
                operation="search.find",
                http_request=FakeRequest(disconnect_after_polls=2),
            )

        # The work must be cancelled, and cancellation must be awaited so its
        # finally blocks release locks and telemetry before we return.
        assert cleanup == ["cleaned"]

    async def test_original_exception_is_not_rewritten(self):
        async def work():
            raise ValueError("upstream blew up")

        with pytest.raises(ValueError, match="upstream blew up"):
            await run_guarded(work, operation="search.find", http_request=FakeRequest())

    async def test_no_tasks_are_leaked(self):
        before = len(asyncio.all_tasks())

        async def work():
            return "done"

        await run_guarded(work, operation="search.find", http_request=FakeRequest())
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


class TestGuardThroughBaseHTTPMiddleware:
    """The guard must survive the app's own `@app.middleware("http")` stack.

    `@app.middleware("http")` is BaseHTTPMiddleware, which hands the endpoint a
    `receive_or_disconnect` wrapper. Starlette's `Request.is_disconnected()`
    polls with an immediately-cancelled scope, and that wrapper needs at least
    one scheduling round to deliver a queued message, so the poll always reports
    "connected" and an abandoned request runs to completion.
    """

    def _app(self, aborted):
        from fastapi import FastAPI, Request
        from starlette.responses import JSONResponse

        from openviking.server.request_guard import run_guarded

        app = FastAPI()

        @app.post("/slow")
        async def slow(http_request: Request):
            async def work():
                # Short enough that the buggy path finishes and fails the
                # assertion, rather than the whole test timing out.
                await asyncio.sleep(2)
                return "finished"

            try:
                await run_guarded(work, operation="test.slow", http_request=http_request)
            except ClientDisconnectedError:
                aborted.append(True)
                return JSONResponse({"aborted": True})
            return JSONResponse({"aborted": False})

        @app.middleware("http")
        async def passthrough(request, call_next):
            return await call_next(request)

        return app

    async def _drive(self, app):
        """Send a request, then a disconnect, straight at the ASGI app."""
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "path": "/slow",
            "raw_path": b"/slow",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"test"), (b"content-length", b"0")],
            "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 80),
            "scheme": "http",
        }
        pending = [
            {"type": "http.request", "body": b"", "more_body": False},
            {"type": "http.disconnect"},
        ]

        async def receive():
            if pending:
                return pending.pop(0)
            await asyncio.sleep(3600)

        async def send(message):
            return None

        await asyncio.wait_for(app(scope, receive, send), timeout=10)

    async def test_disconnect_aborts_through_the_middleware_stack(self):
        aborted = []
        await self._drive(self._app(aborted))
        assert aborted == [True]
