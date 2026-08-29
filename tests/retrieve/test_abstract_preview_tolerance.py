# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""One malformed abstract must not fail an entire query.

Index records hold either a full OKF sidecar (legacy/imported) or an already
body-only abstract (current writers strip it in embedding_utils before
indexing). The two are told apart only by "does the first line say `---`", and a
plain Markdown body may legitimately open with a horizontal rule — so a
body-only abstract starting with `---` gets read as an OKF document whose
frontmatter is never closed, and the whole search returns 500/400.
"""

import pytest

from openviking.core.context import ContextLevel
from openviking.retrieve.hierarchical_retriever import HierarchicalRetriever
from openviking.storage.abstract_overview import (
    body_for_preview,
    body_for_preview_lenient,
    render_abstract_overview,
)

BODY_STARTING_WITH_RULE = "---\n本目录收录缺陷台账。"


class TestLenientPreview:
    def test_strict_version_still_rejects_body_only_content(self):
        # Guards the premise: this is exactly what breaks queries today.
        from openviking.storage.semantic_sidecar import SemanticSidecarFormatError

        with pytest.raises(SemanticSidecarFormatError):
            body_for_preview(BODY_STARTING_WITH_RULE)

    def test_body_only_content_is_returned_unchanged(self):
        assert body_for_preview_lenient(BODY_STARTING_WITH_RULE) == BODY_STARTING_WITH_RULE

    def test_full_okf_document_is_still_reduced_to_its_body(self):
        sidecar = render_abstract_overview(0, "viking://user/zzm/x", "just a summary")

        assert body_for_preview_lenient(sidecar) == "just a summary"

    def test_plain_text_is_returned_unchanged(self):
        assert body_for_preview_lenient("hello world") == "hello world"

    def test_bytes_input_is_decoded(self):
        assert body_for_preview_lenient(BODY_STARTING_WITH_RULE.encode()) == (
            BODY_STARTING_WITH_RULE
        )


class TestConversionSurvivesOneBadRecord:
    """The real call site: one bad candidate must not sink the batch."""

    def _retriever(self):
        return HierarchicalRetriever(storage=None, embedder=None)

    def _candidate(self, uri, abstract):
        return {
            "uri": uri,
            "context_type": "resource",
            "level": ContextLevel.ABSTRACT,
            "abstract": abstract,
            "category": "",
            "_final_score": 0.9,
        }

    async def test_abstract_starting_with_a_rule_does_not_raise(self):
        results = await self._retriever()._convert_to_matched_contexts(
            [self._candidate("viking://user/zzm/a", BODY_STARTING_WITH_RULE)],
            ctx=None,
            apply_hotness=False,
        )

        assert [r.abstract for r in results] == [BODY_STARTING_WITH_RULE]

    async def test_good_records_still_returned_alongside_a_bad_one(self):
        sidecar = render_abstract_overview(0, "viking://user/zzm/b", "clean summary")
        results = await self._retriever()._convert_to_matched_contexts(
            [
                self._candidate("viking://user/zzm/a", BODY_STARTING_WITH_RULE),
                self._candidate("viking://user/zzm/b", sidecar),
            ],
            ctx=None,
            apply_hotness=False,
        )

        assert sorted(r.abstract for r in results) == sorted(
            [BODY_STARTING_WITH_RULE, "clean summary"]
        )
