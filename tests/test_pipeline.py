"""
`interpretation/pipeline.py` (Phase 5-1: 汎用接続基盤) の単体テスト。
elabftw2MaiML_phase5_design.md 15節の統合テスト方針のうち、実フィールド名の
対応表 (Phase 5-2) を必要としない項目を対象にする。
"""
from datetime import datetime, timezone

from elabftw2maiml.interpretation import (
    InterpretationCandidate,
    InterpretationPipeline,
    format_interpretation_report,
)
from elabftw2maiml.model import ExperimentData, Step


def _make_experiment(body_text=None, steps=None):
    return ExperimentData(
        elab_id=1,
        title="テスト実験",
        date=datetime(2026, 9, 16, tzinfo=timezone.utc),
        body_text=body_text,
        steps=steps or [],
    )


def test_free_text_candidates_use_experiment_context_for_body_text():
    exp = _make_experiment(body_text="40℃で30分加熱した。")
    pipeline = InterpretationPipeline()

    candidates = pipeline.free_text_candidates(exp)

    assert len(candidates) == 2  # temperature, duration
    assert all(c.context == "experiment" for c in candidates)
    assert all(c.source == "free_text_regex" for c in candidates)


def test_free_text_candidates_use_step_context_for_step_body():
    exp = _make_experiment(steps=[
        Step(elab_id=101, title="加熱", body="40℃で加熱した。"),
        Step(elab_id=102, title="乾燥", body="80℃で乾燥させた。"),
    ])
    pipeline = InterpretationPipeline()

    candidates = pipeline.free_text_candidates(exp)

    contexts = {c.context for c in candidates}
    assert contexts == {"step:101", "step:102"}


def test_free_text_only_report_has_no_accepted_and_all_unclassified():
    """自由記述だけの場合、role/targetが確定できないため accepted には入らず、
    unclassified に残る (development plan Phase5設計 15節-3)。"""
    exp = _make_experiment(body_text="40℃で30分加熱した。")
    pipeline = InterpretationPipeline()

    report = pipeline.interpret_experiment(exp)

    assert len(report.candidates) == 2
    assert report.accepted == []
    assert report.conflicts == []
    assert len(report.unclassified) == 2


def test_structured_only_candidate_with_role_and_target_is_accepted():
    """構造化フィールドだけの場合 (role/target/semantic_type/contextが確定し、
    confidenceが十分高い) は自動反映可能と判定される (15節-1)。"""
    exp = _make_experiment()
    structured = InterpretationCandidate(
        semantic_type="temperature", value=50, unit="degC", source="custom_field",
        confidence=1.0, context="experiment", role="condition", target="condition_properties",
    )
    pipeline = InterpretationPipeline()

    report = pipeline.interpret_experiment(exp, structured_candidates=[structured])

    assert report.accepted == [structured]
    assert report.conflicts == []
    assert report.unclassified == []


def test_structured_and_free_text_agree_structured_accepted_free_text_unclassified():
    """構造化値と自由記述値が一致する場合 (15節-4): 競合にはならない。構造化側は
    role/targetがあるので自動反映可能、自由記述側はroleが無いのでunclassified。"""
    exp = _make_experiment(body_text="40℃で加熱した。")
    structured = InterpretationCandidate(
        semantic_type="temperature", value=40, unit="degC", source="custom_field",
        confidence=1.0, context="experiment", role="condition", target="condition_properties",
    )
    pipeline = InterpretationPipeline()

    report = pipeline.interpret_experiment(exp, structured_candidates=[structured])

    assert report.conflicts == []
    assert structured in report.accepted
    assert len(report.unclassified) == 1
    assert report.unclassified[0].source == "free_text_regex"


def test_structured_and_free_text_conflict_neither_accepted_nor_unclassified():
    """構造化値と自由記述値が矛盾する場合 (15節-5, development plan 9節の例):
    競合として検出され、どちらもacceptedにもunclassifiedにも入らない
    (上書きされず、conflictsの中にのみ残る)。"""
    exp = _make_experiment(body_text="40℃で30分加熱した。")
    structured = InterpretationCandidate(
        semantic_type="temperature", value=50, unit="degC", source="custom_field",
        confidence=1.0, context="experiment", role="condition", target="condition_properties",
    )
    pipeline = InterpretationPipeline()

    report = pipeline.interpret_experiment(exp, structured_candidates=[structured])

    assert len(report.conflicts) == 1
    conflict = report.conflicts[0]
    assert conflict.semantic_type == "temperature"
    assert {c.value for c in conflict.candidates} == {50, 40}
    # 温度候補はどちらも競合に回収され、accepted/unclassifiedには残らない
    assert structured not in report.accepted
    assert structured not in report.unclassified
    temperature_candidates = [c for c in report.candidates if c.semantic_type == "temperature"]
    assert all(c not in report.accepted and c not in report.unclassified for c in temperature_candidates)
    # durationは矛盾していないので、自由記述側がunclassifiedに残る
    assert any(c.semantic_type == "duration" for c in report.unclassified)


def test_different_steps_do_not_conflict():
    """15節-7: Stepが異なる値は競合しない。"""
    exp = _make_experiment(steps=[
        Step(elab_id=1, title="加熱1", body="40℃で加熱した。"),
        Step(elab_id=2, title="加熱2", body="80℃で加熱した。"),
    ])
    pipeline = InterpretationPipeline()

    report = pipeline.interpret_experiment(exp)

    assert report.conflicts == []


def test_low_confidence_structured_candidate_is_unclassified_not_accepted():
    """confidence_thresholdに満たない場合は、role/targetがあってもacceptedに
    入らずunclassifiedに残る。"""
    exp = _make_experiment()
    structured = InterpretationCandidate(
        semantic_type="temperature", value=50, unit="degC", source="custom_field",
        confidence=0.5, context="experiment", role="condition", target="condition_properties",
    )
    pipeline = InterpretationPipeline(confidence_threshold=1.0)

    report = pipeline.interpret_experiment(exp, structured_candidates=[structured])

    assert report.accepted == []
    assert report.unclassified == [structured]


def test_format_interpretation_report_smoke():
    exp = _make_experiment(body_text="40℃で30分加熱した。")
    structured = InterpretationCandidate(
        semantic_type="temperature", value=50, unit="degC", source="custom_field",
        confidence=1.0, context="experiment", role="condition", target="condition_properties",
    )
    pipeline = InterpretationPipeline()
    report = pipeline.interpret_experiment(exp, structured_candidates=[structured])

    text = format_interpretation_report(report)

    assert "temperature" in text
    assert "custom_field" in text
    assert "free_text_regex" in text
