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
    """グループキーは (semantic_type, context, role, target) の4要素になった
    (コードレビュー 2026-09-17 4.2対応)。ここではいずれの候補もrole/targetが
    未確定 (None) のため、末尾2要素は常にNoneになる。"""
    candidates = [
        _candidate(semantic_type="temperature", context="step:1"),
        _candidate(semantic_type="temperature", context="step:2"),
        _candidate(semantic_type="duration", context=None),
    ]
    groups = group_candidates(candidates)
    assert set(groups.keys()) == {
        ("temperature", "step:1", None, None),
        ("temperature", "step:2", None, None),
        ("duration", None, None, None),
    }


class TestRoleAwareConflictDetection:
    """コードレビュー (2026-09-17) 4.2・8.2の指摘対応: `role`/`target`を
    考慮した競合判定。"""

    def test_different_roles_do_not_conflict(self):
        """同一semantic_type・同一contextでも、roleが確定していて異なる場合は
        別の概念として扱い、競合にしない (例: 材料としてのmass vs 結果としての
        mass)。"""
        candidates = [
            _candidate(semantic_type="mass", value=10, unit="mg",
                       source="custom_field", role="material", target="materials"),
            _candidate(semantic_type="mass", value=9.8, unit="mg",
                       source="custom_field", role="result", target="result_properties"),
        ]
        assert detect_conflicts(candidates) == []

    def test_structured_role_compares_against_unclassified_free_text_role(self):
        """構造化候補のroleが確定していて、自由記述候補のroleが未確定の場合は
        比較対象になり、値が食い違えば競合として検出される。"""
        candidates = [
            _candidate(semantic_type="accelerating_voltage", value=200, unit="kV",
                       source="custom_field", role="condition", target="condition_properties"),
            _candidate(semantic_type="accelerating_voltage", value=250, unit="kV",
                       source="free_text_regex", role=None, target=None),
        ]
        conflicts = detect_conflicts(candidates)
        assert len(conflicts) == 1
        assert conflicts[0].role == "condition"

    def test_same_role_different_value_still_conflicts(self):
        candidates = [
            _candidate(semantic_type="accelerating_voltage", value=200, unit="kV",
                       source="custom_field", role="condition", target="condition_properties"),
            _candidate(semantic_type="accelerating_voltage", value=250, unit="kV",
                       source="free_text_regex", role="condition", target="condition_properties"),
        ]
        conflicts = detect_conflicts(candidates)
        assert len(conflicts) == 1
        assert conflicts[0].role == "condition"

    def test_unclassified_role_free_text_alone_does_not_conflict_with_itself(self):
        """roleが未確定な候補が1件だけの場合は、他に比較対象が無いため競合には
        ならない (通常通り)。"""
        candidates = [
            _candidate(semantic_type="mass", value=10, unit="mg", role=None, target=None),
        ]
        assert detect_conflicts(candidates) == []

    def test_different_targets_do_not_conflict_even_with_same_role(self):
        """targetについてもroleと同様、確定していて異なれば別グループとして
        扱う。"""
        candidates = [
            _candidate(semantic_type="note", value="A", source="custom_field",
                       role="result", target="result_properties"),
            _candidate(semantic_type="note", value="B", source="custom_field",
                       role="result", target="materials"),
        ]
        assert detect_conflicts(candidates) == []


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
