# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Accessors that only want the body must not die on ambiguous content.

`parse_semantic_sidecar` cannot tell "OKF document" from "plain text opening
with a horizontal rule". Call sites that already wrap the strict accessor in
`try/except SemanticSidecarFormatError` made a deliberate choice and keep it;
the ones with no handler must not take a whole operation down over one row.
"""

from unittest.mock import MagicMock

from openviking.retrieve.context_assembler.tiers import overview_from_content
from openviking.service.resource_memory_link_service import ResourceMemoryLinkService

RULE_LEADING = "---\n本目录收录缺陷台账。"


class TestUnprotectedAccessorsAreLenient:
    def test_directory_overview_survives_a_rule_leading_body(self):
        candidate = MagicMock()
        candidate.is_directory = True

        assert overview_from_content(candidate, RULE_LEADING) == RULE_LEADING

    def test_resource_abstract_cleaning_survives_a_rule_leading_body(self):
        cleaned = ResourceMemoryLinkService._clean_resource_abstract(RULE_LEADING)

        assert "缺陷台账" in cleaned


class TestVectorizationSurvivesAmbiguousBodies:
    def test_normalizing_before_index_does_not_raise(self):
        from openviking.storage.semantic_sidecar import body_for_preview_lenient

        # embedding_utils normalizes at this boundary; a body-only abstract that
        # opens with a rule must pass through instead of failing the write.
        assert body_for_preview_lenient(RULE_LEADING) == RULE_LEADING
