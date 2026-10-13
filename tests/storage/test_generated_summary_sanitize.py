# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Model-written summaries must not carry framing that breaks sidecar parsing.

A summary is embedded verbatim into an abstract overview body. `parse_abstract_overview`
tells an abstract overview document from plain text by "is the first line `---`", so a summary
that opens with a horizontal rule — or with a fenced block, which models add
unprompted — produces a sidecar body that later reads as a document whose
frontmatter never closes.
"""

import pytest

from openviking.storage.queuefs.semantic_processor import sanitize_generated_summary


class TestSanitizeGeneratedSummary:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("---\n本目录收录缺陷台账。", "本目录收录缺陷台账。"),
            ("---\n\n正文", "正文"),
            ("***\n正文", "正文"),
            ("```\n正文\n```", "正文"),
            ("```markdown\n正文\n```", "正文"),
            ("  \n\n正文  \n", "正文"),
            ("正文", "正文"),
            ("", ""),
        ],
    )
    def test_leading_framing_is_removed(self, raw, expected):
        assert sanitize_generated_summary(raw) == expected

    def test_a_rule_inside_the_body_is_kept(self):
        # Only leading framing is noise; a rule between paragraphs is content.
        text = "第一段\n\n---\n\n第二段"
        assert sanitize_generated_summary(text) == text

    def test_frontmatter_the_model_invented_is_removed(self):
        raw = "---\ntitle: 缺陷台账\n---\n\n正文内容"
        assert sanitize_generated_summary(raw) == "正文内容"

    def test_result_never_starts_with_a_delimiter(self):
        # The property that actually matters downstream.
        for raw in ["---", "---\n---", "---\n\n---\n\n内容", "```\n---\n内容\n```"]:
            assert not sanitize_generated_summary(raw).startswith("---")
