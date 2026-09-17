"""`elabftw2maiml/interpretation/apply.py` (Phase 5-3: InterpretationReportの
`accepted`候補をExperimentDataへ反映する) の単体テスト。

重点的に確認する点:

1. `target`ごとの反映先 (condition_properties/result_properties/materials/
   instrument) に正しく反映されること。
2. 既に同じキーが存在する場合は上書きせずスキップすること (development plan
   9節「一方で他方を上書きしない」という既存方針をExperimentDataへの反映段階
   でも維持する)。
3. `report.accepted`以外 (`conflicts`/`unclassified`) は絶対に反映しないこと。
4. `materials`への反映は、既存の`elabftw_client.py`のelab_id=0合成アイテムの
   規約に合わせ、複数の候補が1つの合成アイテムにまとめられること。
"""
from datetime import datetime

from elabftw2maiml.model import ExperimentData, LinkedItem, PropertyValue
from elabftw2maiml.interpretation.conflict import InterpretationCandidate, Conflict
from elabftw2maiml.interpretation.pipeline import InterpretationReport, step_context
from elabftw2maiml.interpretation.apply import apply_interpretation_report


def _make_experiment(**overrides) -> ExperimentData:
    defaults = dict(
        elab_id=1,
        title="テスト実験",
        date=datetime(2026, 9, 16),
    )
    defaults.update(overrides)
    return ExperimentData(**defaults)


def _accepted_candidate(**overrides) -> InterpretationCandidate:
    defaults = dict(
        semantic_type="accelerating_voltage",
        value=200,
        source="custom_field",
        confidence=1.0,
        unit="kV",
        context="experiment",
        role="condition",
        target="condition_properties",
    )
    defaults.update(overrides)
    return InterpretationCandidate(**defaults)


class TestConditionAndResultProperties:
    def test_condition_property_is_added(self):
        exp = _make_experiment()
        candidate = _accepted_candidate()
        report = InterpretationReport(accepted=[candidate])

        logs = apply_interpretation_report(exp, report)

        assert len(exp.condition_properties) == 1
        prop = exp.condition_properties[0]
        assert prop.key == "ns1:accelerating_voltage"
        assert prop.value == 200
        assert prop.units == "kV"
        assert prop.xsi_type == "doubleType"
        assert any("反映" in line for line in logs)

    def test_result_property_is_added(self):
        exp = _make_experiment()
        candidate = _accepted_candidate(
            semantic_type="particle_size", value=120, unit="nm",
            role="result", target="result_properties",
        )
        report = InterpretationReport(accepted=[candidate])

        apply_interpretation_report(exp, report)

        assert len(exp.result_properties) == 1
        assert exp.result_properties[0].key == "ns1:particle_size"

    def test_string_value_uses_string_type_and_no_units(self):
        exp = _make_experiment()
        candidate = _accepted_candidate(
            semantic_type="imaging_mode", value="HAADF-STEM", unit=None,
            role="condition", target="condition_properties",
        )
        report = InterpretationReport(accepted=[candidate])

        apply_interpretation_report(exp, report)

        prop = exp.condition_properties[0]
        assert prop.xsi_type == "stringType"
        assert prop.units is None

    def test_duplicate_key_is_skipped_not_overwritten(self):
        exp = _make_experiment(
            condition_properties=[
                PropertyValue(key="ns1:accelerating_voltage", xsi_type="doubleType",
                              value=999, units="kV")
            ]
        )
        candidate = _accepted_candidate(value=200)
        report = InterpretationReport(accepted=[candidate])

        logs = apply_interpretation_report(exp, report)

        # 既存の値 (999) が上書きされず、候補は追加もされない (1件のまま)
        assert len(exp.condition_properties) == 1
        assert exp.condition_properties[0].value == 999
        assert any("スキップ" in line for line in logs)

    def test_custom_ns_prefix(self):
        exp = _make_experiment()
        candidate = _accepted_candidate()
        report = InterpretationReport(accepted=[candidate])

        apply_interpretation_report(exp, report, ns_prefix="mylab")

        assert exp.condition_properties[0].key == "mylab:accelerating_voltage"


class TestMultipleStepsWithSameSemanticType:
    """コードレビュー (2026-09-17) 4.1の指摘対応: 異なるStep由来の同じ
    semantic_typeの値が、後勝ち上書きでもスキップでもなく、両方
    ExperimentDataへ保持されること。"""

    def test_different_step_contexts_are_both_reflected(self):
        exp = _make_experiment()
        candidate_step1 = _accepted_candidate(
            semantic_type="temperature", value=40, unit="degC",
            context=step_context(1),
        )
        candidate_step2 = _accepted_candidate(
            semantic_type="temperature", value=80, unit="degC",
            context=step_context(2),
        )
        report = InterpretationReport(accepted=[candidate_step1, candidate_step2])

        logs = apply_interpretation_report(exp, report)

        assert len(exp.condition_properties) == 2
        by_key = {p.key: p for p in exp.condition_properties}
        assert by_key["ns1:temperature__step_1"].value == 40
        assert by_key["ns1:temperature__step_2"].value == 80
        # どちらも「スキップ」ではなく「反映」ログになっていること。
        assert sum("反映" in line for line in logs) == 2
        assert not any("スキップ" in line for line in logs)

    def test_experiment_context_key_format_is_unchanged(self):
        """既定の実験全体context (`EXPERIMENT_CONTEXT`) では、従来通り
        semantic_typeのみのkeyになる (後方互換性の維持)。"""
        exp = _make_experiment()
        candidate = _accepted_candidate(context="experiment")
        report = InterpretationReport(accepted=[candidate])

        apply_interpretation_report(exp, report)

        assert exp.condition_properties[0].key == "ns1:accelerating_voltage"

    def test_same_step_context_still_dedupes(self):
        """同じStep (同じcontext) の同じsemantic_typeが2回反映されようとした
        場合は、これまで通り重複としてスキップされる (Step単位の一意性は
        壊さない)。"""
        exp = _make_experiment()
        candidate_a = _accepted_candidate(
            semantic_type="temperature", value=40, unit="degC",
            context=step_context(1),
        )
        candidate_b = _accepted_candidate(
            semantic_type="temperature", value=41, unit="degC",
            context=step_context(1),
        )
        report = InterpretationReport(accepted=[candidate_a, candidate_b])

        logs = apply_interpretation_report(exp, report)

        assert len(exp.condition_properties) == 1
        assert exp.condition_properties[0].value == 40
        assert any("スキップ" in line for line in logs)


class TestMaterialsTarget:
    def test_creates_synthetic_material_when_none_exists(self):
        exp = _make_experiment()
        candidate = _accepted_candidate(
            semantic_type="sample_id", value="S-001", unit=None,
            role="material", target="materials",
        )
        report = InterpretationReport(accepted=[candidate])

        apply_interpretation_report(exp, report)

        assert len(exp.materials) == 1
        synthetic = exp.materials[0]
        assert synthetic.elab_id == 0
        assert len(synthetic.properties) == 1
        assert synthetic.properties[0].key == "ns1:sample_id"

    def test_reuses_existing_synthetic_material_elab_id_zero(self):
        """既存の`elabftw_client.py`のグループ名ベースの振り分けが、既に
        elab_id=0の合成LinkedItemを作っている場合、それを再利用して
        2つに分裂させないことを確認する。"""
        existing = LinkedItem(
            elab_id=0, title="テスト実験", category="(experiment own MATERIAL fields)",
            properties=[PropertyValue(key="ns1:Grid", xsi_type="stringType", value="Square")],
        )
        exp = _make_experiment(materials=[existing])
        candidate = _accepted_candidate(
            semantic_type="sample_id", value="S-001", unit=None,
            role="material", target="materials",
        )
        report = InterpretationReport(accepted=[candidate])

        apply_interpretation_report(exp, report)

        assert len(exp.materials) == 1
        assert len(exp.materials[0].properties) == 2

    def test_duplicate_material_property_is_skipped(self):
        existing = LinkedItem(
            elab_id=0, title="テスト実験", category="(interpretation-derived material)",
            properties=[PropertyValue(key="ns1:sample_id", xsi_type="stringType", value="S-001")],
        )
        exp = _make_experiment(materials=[existing])
        candidate = _accepted_candidate(
            semantic_type="sample_id", value="S-002", unit=None,
            role="material", target="materials",
        )
        report = InterpretationReport(accepted=[candidate])

        logs = apply_interpretation_report(exp, report)

        assert len(exp.materials[0].properties) == 1
        assert exp.materials[0].properties[0].value == "S-001"
        assert any("スキップ" in line for line in logs)


class TestInstrumentTarget:
    def test_instrument_party_is_added(self):
        exp = _make_experiment()
        candidate = _accepted_candidate(
            semantic_type="instrument_name", value="JEM-ARM200F", unit=None,
            role="instrument", target="instrument",
        )
        report = InterpretationReport(accepted=[candidate])

        apply_interpretation_report(exp, report)

        assert len(exp.instruments) == 1
        assert exp.instruments[0].name == "JEM-ARM200F"

    def test_duplicate_instrument_name_is_skipped(self):
        from elabftw2maiml.model import Party
        exp = _make_experiment(instruments=[Party(key="x", name="JEM-ARM200F")])
        candidate = _accepted_candidate(
            semantic_type="instrument_name", value="JEM-ARM200F", unit=None,
            role="instrument", target="instrument",
        )
        report = InterpretationReport(accepted=[candidate])

        logs = apply_interpretation_report(exp, report)

        assert len(exp.instruments) == 1
        assert any("スキップ" in line for line in logs)


class TestUnsupportedTarget:
    def test_unknown_target_is_skipped_with_log_not_dropped_silently(self):
        exp = _make_experiment()
        candidate = _accepted_candidate(target="something_unexpected")
        report = InterpretationReport(accepted=[candidate])

        logs = apply_interpretation_report(exp, report)

        assert exp.condition_properties == []
        assert exp.result_properties == []
        assert exp.materials == []
        assert any("未対応" in line for line in logs)


class TestOnlyAcceptedIsApplied:
    def test_conflicts_are_never_written(self):
        exp = _make_experiment()
        conflicting_candidate = _accepted_candidate(value=999)
        conflict = Conflict(
            semantic_type="accelerating_voltage", context="experiment",
            candidates=(conflicting_candidate, _accepted_candidate(value=200)),
        )
        report = InterpretationReport(
            candidates=[conflicting_candidate],
            accepted=[],  # 競合候補はpartition_candidates()によりacceptedに入らない
            conflicts=[conflict],
        )

        apply_interpretation_report(exp, report)

        assert exp.condition_properties == []

    def test_unclassified_is_never_written(self):
        exp = _make_experiment()
        unclassified_candidate = _accepted_candidate(role=None, target=None)
        report = InterpretationReport(
            candidates=[unclassified_candidate],
            accepted=[],
            unclassified=[unclassified_candidate],
        )

        apply_interpretation_report(exp, report)

        assert exp.condition_properties == []

    def test_empty_report_is_a_noop(self):
        exp = _make_experiment()
        report = InterpretationReport()

        logs = apply_interpretation_report(exp, report)

        assert logs == []
        assert exp.condition_properties == []
        assert exp.result_properties == []
        assert exp.materials == []
        assert exp.instruments == []
