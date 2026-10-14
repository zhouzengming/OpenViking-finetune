# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""A permission error must not poison the shared semantic queue.

The semantic worker is a single process-wide instance whose circuit breaker is
shared by every account, so one message that can never succeed must be dropped
rather than retried forever.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from openviking.storage.queuefs.named_queue import NamedQueue
from openviking.storage.queuefs.process_result import ProcessResult
from openviking.storage.queuefs.semantic_msg import SemanticMsg
from openviking.storage.queuefs.semantic_processor import SemanticProcessor
from openviking_cli.exceptions import PermissionDeniedError

MODULE = "openviking.storage.queuefs.semantic_processor"
# _enqueue_parent_refresh imports the queue manager lazily from the package.
QUEUE_PKG = "openviking.storage.queuefs"


class TestParentRefreshBoundary:
    """Parent refresh must stop at the user namespace root."""

    # 上游 v0.4.17 把上溯逻辑重构成 plan_abstract_overview_refresh，旧的
    # mark_semantic_sidecars_pending 已不存在。停止判定发生在调用它**之前**，
    # 所以"有没有越过边界"等价于"这个函数有没有被调到"。

    async def test_stops_at_user_namespace_root(self):
        # viking://user is the container holding every account, and a per-user
        # identity cannot write its .overview.md. Walking up to it produces a
        # message that fails forever.
        msg = SemanticMsg(uri="viking://user/zzm", context_type="resource")

        with (
            patch(f"{MODULE}.plan_abstract_overview_refresh", new=AsyncMock()) as plan,
            patch(f"{MODULE}.get_viking_fs", new=MagicMock()),
            patch(f"{QUEUE_PKG}.get_queue_manager", new=MagicMock(return_value=None)),
        ):
            await SemanticProcessor()._enqueue_parent_refresh(
                msg, "viking://user/zzm", l0_body_changed=True
            )

        plan.assert_not_called()

    async def test_still_refreshes_within_the_user_tree(self):
        # The boundary must not stop refreshes of the account's own subtree.
        msg = SemanticMsg(uri="viking://user/zzm/memories", context_type="resource")

        # 上游在这条继续路径上新增了 get_openviking_config()，测试环境没有配置
        # 文件；停止路径在它之前就 return，所以只有这条需要 patch。
        with (
            patch(f"{MODULE}.plan_abstract_overview_refresh", new=AsyncMock()) as plan,
            patch(f"{MODULE}.get_viking_fs", new=MagicMock()),
            patch(f"{MODULE}.get_openviking_config", new=MagicMock()),
            patch(f"{QUEUE_PKG}.get_queue_manager", new=MagicMock(return_value=None)),
        ):
            await SemanticProcessor()._enqueue_parent_refresh(
                msg, "viking://user/zzm/memories", l0_body_changed=True
            )

        assert plan.await_args.kwargs["dir_uri"] == "viking://user/zzm"


class TestPermissionDeniedIsDropped:
    """A storage-layer permission error is permanent, not transient."""

    def _processor_and_msg(self):
        processor = SemanticProcessor()
        processor._requeue_semantic_msg_after_error = AsyncMock()
        msg = SemanticMsg(uri="viking://user/zzm/memories/a.md", context_type="resource")
        breaker = processor._account_breaker(msg.account_id)
        breaker.record_failure = MagicMock()
        return processor, msg, breaker

    async def test_not_re_enqueued_and_returns_failed_process_result(self):
        processor, msg, _ = self._processor_and_msg()
        boom = PermissionDeniedError("write .overview.md denied")

        with (
            patch(f"{MODULE}.is_semantic_msg_stale", side_effect=boom),
            patch.object(processor, "_cleanup_local_artifact", new=AsyncMock()) as cleanup,
        ):
            result = await processor.on_dequeue(msg.to_dict())

        assert NamedQueue._validate_result(result) == ProcessResult.failed(str(boom))
        processor._requeue_semantic_msg_after_error.assert_not_awaited()
        cleanup.assert_awaited_once()

    async def test_does_not_trip_the_account_circuit_breaker(self):
        # Tripping the circuit breaker on a storage permission error would stall
        # subsequent valid semantic indexing messages.
        processor, msg, breaker = self._processor_and_msg()
        boom = PermissionDeniedError("write .overview.md denied")

        with patch(f"{MODULE}.is_semantic_msg_stale", side_effect=boom):
            await processor.on_dequeue(msg.to_dict())

        breaker.record_failure.assert_not_called()
