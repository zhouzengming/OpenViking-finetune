# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A permission error must not poison the shared semantic queue.

The semantic worker is a single process-wide instance whose circuit breaker is
shared by every account, so one message that can never succeed must be dropped
rather than retried forever.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking_cli.exceptions import PermissionDeniedError

MODULE = "openviking.storage.queuefs.semantic_processor"
# _enqueue_parent_refresh imports the queue manager lazily from the package.
QUEUE_PKG = "openviking.storage.queuefs"


class TestParentRefreshBoundary:
    """Parent refresh must stop at the user namespace root."""

    async def test_stops_at_user_namespace_root(self):
        # viking://user is the container holding every account, and a per-user
        # identity cannot write its .overview.md. Walking up to it produces a
        # message that fails forever.
        msg = SemanticMsg(uri="viking://user/zzm", context_type="resource")

        with (
            patch(f"{MODULE}.mark_semantic_sidecars_pending", new=AsyncMock()) as mark_pending,
            patch(f"{MODULE}.get_viking_fs", new=MagicMock()),
            patch(f"{QUEUE_PKG}.get_queue_manager", new=MagicMock(return_value=None)),
        ):
            await SemanticProcessor()._enqueue_parent_refresh(msg, "viking://user/zzm")

        mark_pending.assert_not_called()

    async def test_still_refreshes_within_the_user_tree(self):
        # The boundary must not stop refreshes of the account's own subtree.
        msg = SemanticMsg(uri="viking://user/zzm/memories", context_type="resource")

        with (
            patch(f"{MODULE}.mark_semantic_sidecars_pending", new=AsyncMock()) as mark_pending,
            patch(f"{MODULE}.get_viking_fs", new=MagicMock()),
            patch(f"{QUEUE_PKG}.get_queue_manager", new=MagicMock(return_value=None)),
        ):
            await SemanticProcessor()._enqueue_parent_refresh(msg, "viking://user/zzm/memories")

        assert mark_pending.await_args.kwargs["dir_uri"] == "viking://user/zzm"


class TestPermissionDeniedIsDropped:
    """A storage-layer permission error is permanent, not transient."""

    def _processor_and_msg(self):
        processor = SemanticProcessor()
        processor._requeue_semantic_msg_after_error = AsyncMock()
        processor._circuit_breaker = MagicMock()
        processor.report_error = MagicMock()
        msg = SemanticMsg(uri="viking://user/zzm/memories/a.md", context_type="resource")
        return processor, msg

    async def test_not_re_enqueued(self):
        processor, msg = self._processor_and_msg()
        boom = PermissionDeniedError("write .overview.md denied")

        with patch(f"{MODULE}.is_semantic_msg_stale", side_effect=boom):
            assert await processor.on_dequeue(msg.to_dict()) is None

        processor._requeue_semantic_msg_after_error.assert_not_awaited()
        processor.report_error.assert_called_once()

    async def test_does_not_trip_the_shared_circuit_breaker(self):
        # The breaker is shared by every account; tripping it here stalls
        # semantic indexing for all of them.
        processor, msg = self._processor_and_msg()
        boom = PermissionDeniedError("write .overview.md denied")

        with patch(f"{MODULE}.is_semantic_msg_stale", side_effect=boom):
            await processor.on_dequeue(msg.to_dict())

        processor._circuit_breaker.record_failure.assert_not_called()
