"""
`detect_conflicts()` / `format_conflict_report()` の単体テスト。
elabftw2MaiML_phase1_development_plan.md 9節・12節 (Phase 4) の例に基づく。
"""
from elabftw2maiml.interpretation import (
    Conflict,
    InterpretationCandidate,
    detect_conflicts,
    format_conflict_report,
    group_candidates,
)


def _candidate(semantic_type="temperature", value=40, source="free_text", **kwargs):
    return InterpretationCandidate(semantic_type=semantic_type, value=value, source=source, **kwargs)


def test_development_plan_worked_example_is_detected_as_conflict():
    """development plan 9節の例:
    Custom Field: Temperature = 50℃ / 自由記述: 40℃で30分加熱した。
    -> 一致しないため競合として検出される。"""
    candidates = [
        InterpretationCandidate(semantic_type="temperature", value=50, unit="degC",
                                 source="custom_field"),
        InterpretationCandidate(semantic_type="temperature", value=40, unit="degC",
                                 source="free_text", source_text="40℃で30分加熱した。"),
    ]

    conflicts = detect_conflicts(candidates)

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.semantic_type == "temperature"
    assert conflict.context is None
    assert conflict.candidates == tuple(candidates)


def test_agreeing_candidates_are_not_reported_as_conflicts():
    candidates = [
        _candidate(value=40, unit="degC", source="custom_field"),
        _candidate(value=40, unit="degC", source="free_text", source_text="40℃で加熱した。"),
    ]
    assert detect_conflicts(candidates) == []


def test_single_candidate_is_never_a_conflict():
    candidates = [_candidate(value=40, unit="degC", source="free_text")]
    assert detect_conflicts(candidates) == []


def test_different_semantic_types_are_independent_groups():
    candidates = [
        _candidate(semantic_type="temperature", value=50, unit="degC", source="custom_field"),
        _candidate(semantic_type="temperature", value=40, unit="degC", source="free_text"),
        _candidate(semantic_type="duration", value=30, unit="min", source="free_text"),
    ]
    conflicts = detect_conflicts(candidates)
    assert len(conflicts) == 1
    assert conflicts[0].semantic_type == "temperature"


def test_context_distinguishes_otherwise_identical_semantic_types():
    """同じ semantic_type でも context (例: どのStepの値か) が異なれば
    別グループとして扱い、互いに競合しない。"""
    candidates = [
        _candidate(value=40, unit="degC", source="free_text", context="step:1"),
        _candidate(value=80, unit="degC", source="free_text", context="step:2"),
    ]
    assert detect_conflicts(candidates) == []


def test_context_scoped_conflict_is_still_detected():
    candidates = [
        _candidate(value=40, unit="degC", source="custom_field", context="step:1"),
        _candidate(value=45, unit="degC", source="free_text", context="step:1"),
    ]
    conflicts = detect_conflicts(candidates)
    assert len(conflicts) == 1
    assert conflicts[0].context == "step:1"


def test_same_value_different_unit_is_treated_as_a_conflict():
    # 単位変換は行わないため、数値が同じでも単位が異なれば一致とはみなさない
    candidates = [
        _candidate(value=300, unit="K", source="custom_field"),
        _candidate(value=300, unit="degC", source="free_text"),
    ]
    conflicts = detect_conflicts(candidates)
    assert len(conflicts) == 1


def test_group_candidates_groups_by_semantic_type_and_context():
    candidates = [
        _candidate(semantic_type="temperature", context="step:1"),
        _candidate(semantic_type="temperature", context="step:2"),
        _candidate(semantic_type="duration", context=None),
    ]
    groups = group_candidates(candidates)
    assert set(groups.keys()) == {("temperature", "step:1"), ("temperature", "step:2"), ("duration", None)}


def test_format_conflict_report_with_no_conflicts():
    assert format_conflict_report([]) == "検出された競合はありません。"


def test_format_conflict_report_includes_source_and_source_text():
    conflict = Conflict(
        semantic_type="temperature",
        context=None,
        candidates=(
            InterpretationCandidate(semantic_type="temperature", value=50, unit="degC",
                                     source="custom_field"),
            InterpretationCandidate(semantic_type="temperature", value=40, unit="degC",
                                     source="free_text", source_text="40℃で30分加熱した。"),
        ),
    )
    report = format_conflict_report([conflict])
    assert "temperature" in report
    assert "custom_field" in report
    assert "free_text" in report
    assert "40℃で30分加熱した。" in report
