"""
Phase 5-3 (interpretation -> ExperimentDataへの反映 -> MaiML生成) の
統合テスト。elabftw2MaiML_phase5_design.mdのPhase 5-3の6項目のうち、
実際のeLabFTWサーバーを必要としない部分 (1〜5) を、合成データで一通り確認する
(実データでの確認はPhase 5-4で別途行う)。

シナリオ:
    - 実験のカスタムフィールド (構造化): AcceleratingVoltage(kV)=200,
      Magnification=200000, sampleID="HS-100MG001"
    - 実験本文 (自由記述): 「加速電圧250kVに変更して再測定した。倍率200,000倍で
      観察した。」
        - 加速電圧: 構造化(200kV) と 自由記述(250kV) が食い違う -> 競合として
          検出され、どちらもExperimentDataには反映されない (自動採用しない)。
        - 倍率: 構造化(200,000) と 自由記述(200,000) が一致する -> 競合ではない。
          構造化側 (role/target確定済み) のみが自動反映される。自由記述側は
          role/targetが未確定のままのため unclassified に残る (念のための
          突き合わせ用の重複として残るだけで、二重に書き込まれることはない)。
        - 試料ID: 自由記述からの言及は無く、構造化フィールドのみ -> 競合なしで
          自動反映される (materials への合成アイテムとして)。
"""
from datetime import datetime
from pathlib import Path

import pytest
from lxml import etree

from elabftw2maiml.model import ExperimentData
from elabftw2maiml.builder import MaimlBuilder
from elabftw2maiml.interpretation import (
    RawField,
    FieldMapping,
    build_structured_candidates,
    InterpretationPipeline,
    apply_interpretation_report,
    format_interpretation_report,
    SemTemTextRuleInterpreter,
)

SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "maiml.xsd"

FIELD_MAPPING = FieldMapping.from_dict({
    "fields": {
        "AcceleratingVoltage(kV)": {
            "semantic_type": "accelerating_voltage",
            "role": "condition",
            "unit": "kV",
            "context": "experiment",
            "target": "condition_properties",
            "required": True,
        },
        "Magnification": {
            "semantic_type": "magnification",
            "role": "condition",
            "unit": "x",
            "context": "experiment",
            "target": "condition_properties",
        },
        "sampleID": {
            "semantic_type": "sample_id",
            "role": "material",
            "context": "experiment",
            "target": "materials",
        },
    }
})


def _build_experiment_and_report():
    experiment = ExperimentData(
        elab_id=42,
        title="STEM観察 (合成テスト)",
        date=datetime(2026, 9, 16),
        body_text="加速電圧250kVに変更して再測定した。倍率200,000倍で観察した。",
    )

    raw_fields = [
        RawField(name="AcceleratingVoltage(kV)", value=200, group="CONDITION"),
        RawField(name="Magnification", value=200000, group="CONDITION"),
        RawField(name="sampleID", value="HS-100MG001", group="MATERIAL"),
    ]
    structured_candidates, unmapped = build_structured_candidates(raw_fields, FIELD_MAPPING)
    assert unmapped == []

    pipeline = InterpretationPipeline(extra_text_interpreters=[SemTemTextRuleInterpreter()])
    report = pipeline.interpret_experiment(experiment, structured_candidates=structured_candidates)
    return experiment, report


class TestReportShapeBeforeApply:
    """反映前の`InterpretationReport`自体が、意図した仕分けになっていることを
    確認する (elabftw2MaiML_phase5_design.md Phase5-3 項目1・3・4に対応)。"""

    def test_accelerating_voltage_conflict_detected(self):
        _experiment, report = _build_experiment_and_report()
        conflict_types = {c.semantic_type for c in report.conflicts}
        assert "accelerating_voltage" in conflict_types

    def test_conflicting_candidates_are_not_in_accepted(self):
        _experiment, report = _build_experiment_and_report()
        accepted_types = {c.semantic_type for c in report.accepted}
        assert "accelerating_voltage" not in accepted_types

    def test_magnification_structured_candidate_is_accepted(self):
        _experiment, report = _build_experiment_and_report()
        accepted = [c for c in report.accepted if c.semantic_type == "magnification"]
        assert len(accepted) == 1
        assert accepted[0].source == "custom_field"
        assert accepted[0].value == 200000

    def test_magnification_free_text_duplicate_is_unclassified_not_accepted(self):
        _experiment, report = _build_experiment_and_report()
        magnification_candidates = [c for c in report.candidates if c.semantic_type == "magnification"]
        assert len(magnification_candidates) == 2  # 構造化1件 + 自由記述1件
        unclassified_sources = {c.source for c in report.unclassified if c.semantic_type == "magnification"}
        assert "free_text_regex" in unclassified_sources

    def test_sample_id_is_accepted_with_no_free_text_counterpart(self):
        _experiment, report = _build_experiment_and_report()
        accepted = [c for c in report.accepted if c.semantic_type == "sample_id"]
        assert len(accepted) == 1
        assert accepted[0].value == "HS-100MG001"

    def test_formatted_report_mentions_conflict(self):
        _experiment, report = _build_experiment_and_report()
        text = format_interpretation_report(report)
        assert "競合" in text
        assert "accelerating_voltage" in text


class TestApplyThenBuild:
    """`apply_interpretation_report()`でExperimentDataへ反映し、
    `MaimlBuilder`でMaiMLを生成できることを確認する
    (elabftw2MaiML_phase5_design.md Phase5-3 項目2・5に対応)。"""

    def test_conflicting_value_is_absent_from_experiment_data(self):
        experiment, report = _build_experiment_and_report()
        apply_interpretation_report(experiment, report)

        keys = {p.key for p in experiment.condition_properties}
        assert "ns1:accelerating_voltage" not in keys

    def test_agreed_value_is_present_exactly_once(self):
        experiment, report = _build_experiment_and_report()
        apply_interpretation_report(experiment, report)

        magnification_props = [
            p for p in experiment.condition_properties if p.key == "ns1:magnification"
        ]
        assert len(magnification_props) == 1
        assert magnification_props[0].value == 200000

    def test_sample_id_is_present_in_synthetic_material(self):
        experiment, report = _build_experiment_and_report()
        apply_interpretation_report(experiment, report)

        assert len(experiment.materials) == 1
        material = experiment.materials[0]
        assert material.elab_id == 0
        keys = {p.key for p in material.properties}
        assert "ns1:sample_id" in keys

    def test_builder_produces_well_formed_xml(self):
        experiment, report = _build_experiment_and_report()
        apply_interpretation_report(experiment, report)

        builder = MaimlBuilder(ns_prefix="ns1", ns_uri="https://example.org/maiml/mylab",
                                elab_host="elab.example.org")
        xml_bytes = builder.to_bytes(experiment)
        # 構文的に妥当なXMLであることの確認 (壊れていればここで例外が送出される)
        root = etree.fromstring(xml_bytes)
        assert root is not None

    def test_generated_xml_contains_accepted_values_and_not_conflicting_ones(self):
        experiment, report = _build_experiment_and_report()
        apply_interpretation_report(experiment, report)

        builder = MaimlBuilder(ns_prefix="ns1", ns_uri="https://example.org/maiml/mylab",
                                elab_host="elab.example.org")
        xml_bytes = builder.to_bytes(experiment)
        xml_text = xml_bytes.decode("utf-8")

        assert "200000" in xml_text  # 倍率 (合意した値) は反映されている
        assert "HS-100MG001" in xml_text  # 試料ID (競合の無い値) も反映されている
        # 競合した加速電圧の値 (200/250) は、どちらもproperty値としては書き込まれない。
        # (自由記述の原文 "250" 等は実験本文プロパティ内に残り得るため、単純な文字列
        # 不在チェックではなく、condition_propertiesの中身で既に確認済み)

    @pytest.mark.skipif(not SCHEMA_PATH.exists(), reason=(
        f"{SCHEMA_PATH} が見つからないため、XSDによるスキーマ検証をスキップします。"
        "MaiML-Schema-1_0 のXSDを schemas/maiml.xsd に配置すると有効になります。"
    ))
    def test_generated_xml_validates_against_xsd(self):
        experiment, report = _build_experiment_and_report()
        apply_interpretation_report(experiment, report)

        builder = MaimlBuilder(ns_prefix="ns1", ns_uri="https://example.org/maiml/mylab",
                                elab_host="elab.example.org")
        xml_bytes = builder.to_bytes(experiment)

        schema = etree.XMLSchema(etree.parse(str(SCHEMA_PATH)))
        doc = etree.fromstring(xml_bytes)
        assert schema.validate(doc), schema.error_log
