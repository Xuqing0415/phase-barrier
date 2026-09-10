# -*- coding: utf-8 -*-
"""配置校验边界测试（v0.54.1）：补齐语义 / 五道防线配置项的合法与非法取值分支。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from anti_shortcut.config import (
    DualReviewOptions,
    FormalCheckOptions,
    MutationScoreOptions,
    RequirementCoverageOptions,
    RequirementTemplateOptions,
    SpecSpecificityOptions,
    load_config,
)


def test_requirement_coverage_bounds_and_stages():
    opts = RequirementCoverageOptions(min_coverage=75.0, stages=[1, 4])
    assert opts.min_coverage == 75.0 and opts.stages == [1, 4]
    with pytest.raises(ValidationError):
        RequirementCoverageOptions(min_coverage=101.0)
    with pytest.raises(ValidationError):
        RequirementCoverageOptions(stages=[])
    with pytest.raises(ValidationError):
        RequirementCoverageOptions(stages=[7])
    with pytest.raises(ValidationError):
        RequirementCoverageOptions(stages=["not-an-int"])


def test_mutation_score_bounds_and_stages():
    opts = MutationScoreOptions(min_score=90.0, max_mutants=3, timeout_per_mutant=1.5, stages=[4])
    assert opts.max_mutants == 3
    with pytest.raises(ValidationError):
        MutationScoreOptions(min_score=-1.0)
    with pytest.raises(ValidationError):
        MutationScoreOptions(max_mutants=0)
    with pytest.raises(ValidationError):
        MutationScoreOptions(timeout_per_mutant=0)
    with pytest.raises(ValidationError):
        MutationScoreOptions(stages=[])
    with pytest.raises(ValidationError):
        MutationScoreOptions(stages=[9])


def test_spec_specificity_bounds_and_filler_patterns():
    opts = SpecSpecificityOptions(
        min_entities=0, max_filler_hits=0, filler_patterns=["综合.*因素"], stages=[1]
    )
    assert opts.min_entities == 0 and opts.filler_patterns == ["综合.*因素"]
    with pytest.raises(ValidationError):
        SpecSpecificityOptions(min_entities=-1)
    with pytest.raises(ValidationError):
        SpecSpecificityOptions(filler_patterns=[])
    with pytest.raises(ValidationError):
        SpecSpecificityOptions(filler_patterns=["   "])
    with pytest.raises(ValidationError):
        SpecSpecificityOptions(stages=[])
    with pytest.raises(ValidationError):
        SpecSpecificityOptions(stages=[99])


def test_requirement_template_min_item_bounds():
    opts = RequirementTemplateOptions(
        goal_max_chars=120, min_forbidden_items=2, min_interface_items=3, min_acceptance_items=4
    )
    assert opts.min_acceptance_items == 4
    with pytest.raises(ValidationError):
        RequirementTemplateOptions(min_forbidden_items=0)
    with pytest.raises(ValidationError):
        RequirementTemplateOptions(goal_max_chars=0)


def test_dual_review_option_bounds():
    opts = DualReviewOptions(timeout_seconds=1.0, max_retries=1, min_confidence=0.5)
    assert opts.min_confidence == 0.5
    with pytest.raises(ValidationError):
        DualReviewOptions(timeout_seconds=0)
    with pytest.raises(ValidationError):
        DualReviewOptions(max_retries=0)
    with pytest.raises(ValidationError):
        DualReviewOptions(min_confidence=1.5)


def test_formal_check_timeout_bound():
    assert FormalCheckOptions(tlc_timeout_seconds=5.0).tlc_timeout_seconds == 5.0
    with pytest.raises(ValidationError):
        FormalCheckOptions(tlc_timeout_seconds=0)


def test_load_config_rejects_non_mapping_file_and_unsupported_type(tmp_path):
    bad = tmp_path / "list.yaml"
    bad.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(bad)
    with pytest.raises(TypeError):
        load_config(12345)
