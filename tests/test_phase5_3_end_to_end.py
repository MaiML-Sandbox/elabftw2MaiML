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
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from elabftw2maiml.model import ExperimentData
from elabftw2maiml.builder import MaimlBuilder
from elabftw2maiml.interpretation import (
    RawField,
    FieldMapping,
    build_structured_candidates,
    candidate_from_field,
    InterpretationPipeline,
    InterpretationReport,
    apply_interpretation_report,
    format_interpretation_report,
    SemTemTextRuleInterpreter,
    step_context,
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


class TestRealisticStringValuesWithEmbeddedUnits:
    """Phase 5-3 fix (単位正規化): 実際のeLabFTWのExtra Fieldsは値と単位を分けて
    持つ仕組みが無く、"200 kV"のように単位混在の文字列で返ってくることがある
    (実際の画面例で確認済み)。正規化を行わない場合、これは自由記述側の数値
    (int/型)と表現が食い違い、実際に一致している値が誤って競合と判定されて
    しまっていた。この節では、その修正が効いていることを確認する。"""

    def _build(self, accelerating_voltage_raw_value, body_text):
        experiment = ExperimentData(
            elab_id=43,
            title="STEM観察 (単位混在文字列テスト)",
            date=datetime(2026, 9, 16),
            body_text=body_text,
        )
        raw_fields = [
            RawField(name="AcceleratingVoltage(kV)", value=accelerating_voltage_raw_value,
                      group="CONDITION"),
        ]
        structured_candidates, unmapped = build_structured_candidates(raw_fields, FIELD_MAPPING)
        assert unmapped == []

        pipeline = InterpretationPipeline(extra_text_interpreters=[SemTemTextRuleInterpreter()])
        report = pipeline.interpret_experiment(experiment, structured_candidates=structured_candidates)
        return experiment, report

    def test_embedded_unit_string_agrees_with_matching_free_text(self):
        """"200 kV" (文字列) と自由記述「加速電圧200 kVで観察した」は、本来
        一致しているはずの値。正規化前は型・表現の違いから誤って競合と判定
        されていたが、修正後は一致として扱われ、自動反映される。"""
        experiment, report = self._build(
            accelerating_voltage_raw_value="200 kV",
            body_text="加速電圧200 kVで観察した。",
        )

        assert report.conflicts == []
        accepted = [c for c in report.accepted if c.semantic_type == "accelerating_voltage"]
        assert len(accepted) == 1
        assert accepted[0].value == Decimal("200")

        apply_interpretation_report(experiment, report)
        keys = {p.key: p for p in experiment.condition_properties}
        assert "ns1:accelerating_voltage" in keys
        assert keys["ns1:accelerating_voltage"].value == Decimal("200")
        assert keys["ns1:accelerating_voltage"].units == "kV"

    def test_embedded_unit_string_still_conflicts_with_disagreeing_free_text(self):
        """値が実際に食い違う場合 (200 kV vs 250 kV) は、正規化後も正しく
        競合として検出されること (正規化が食い違い検出そのものを無効化して
        いないことの確認)。"""
        experiment, report = self._build(
            accelerating_voltage_raw_value="200 kV",
            body_text="加速電圧250kVに変更して再測定した。",
        )

        conflict_types = {c.semantic_type for c in report.conflicts}
        assert "accelerating_voltage" in conflict_types
        accepted_types = {c.semantic_type for c in report.accepted}
        assert "accelerating_voltage" not in accepted_types

        apply_interpretation_report(experiment, report)
        keys = {p.key for p in experiment.condition_properties}
        assert "ns1:accelerating_voltage" not in keys

    def test_dimension_mismatched_value_is_unclassified_not_falsely_matched_or_conflicted(self):
        """"200 mA" (期待単位kVと次元が異なる) は、自由記述に何も言及が無くても
        自動反映されず (unclassifiedに残る)、誤って「一致」とみなされたり
        クラッシュしたりしないこと。"""
        experiment, report = self._build(
            accelerating_voltage_raw_value="200 mA",
            body_text="",
        )

        assert report.conflicts == []
        accepted_types = {c.semantic_type for c in report.accepted}
        assert "accelerating_voltage" not in accepted_types
        unclassified = [c for c in report.unclassified if c.semantic_type == "accelerating_voltage"]
        assert len(unclassified) == 1
        assert unclassified[0].raw_value == "200 mA"
        assert unclassified[0].reason is not None

        apply_interpretation_report(experiment, report)
        assert experiment.condition_properties == []

    def test_non_numeric_value_is_unclassified_and_logged_not_dropped(self):
        """"not measured" のような非数値の値も、クラッシュせず未分類として
        原値を保持したまま残ること。"""
        experiment, report = self._build(
            accelerating_voltage_raw_value="not measured",
            body_text="",
        )

        unclassified = [c for c in report.unclassified if c.semantic_type == "accelerating_voltage"]
        assert len(unclassified) == 1
        assert unclassified[0].value == "not measured"

        text = format_interpretation_report(report)
        assert "not measured" in text


class TestMultipleStepsSameSemanticTypeEndToEnd:
    """コードレビュー (2026-09-17) 4.1・8.1の指摘対応: 異なるStepの同じ
    semantic_typeの構造化候補が、両方ExperimentDataへ反映され、生成された
    MaiMLにも両方出力されることを確認する統合テスト。"""

    STEP_FIELD_MAPPING = FieldMapping.from_dict({
        "fields": {
            "Temperature(degC)": {
                "semantic_type": "temperature",
                "role": "condition",
                "unit": "degC",
                "target": "condition_properties",
            },
        }
    })

    def test_step1_and_step2_temperature_both_reflected_and_built(self):
        experiment = ExperimentData(
            elab_id=50,
            title="複数Step温度記録テスト",
            date=datetime(2026, 9, 16),
            body_text="",
        )

        step1_raw = RawField(name="Temperature(degC)", value="40 degC")
        step2_raw = RawField(name="Temperature(degC)", value="80 degC")
        candidate_step1 = candidate_from_field(
            step1_raw, self.STEP_FIELD_MAPPING, context_override=step_context(1),
        )
        candidate_step2 = candidate_from_field(
            step2_raw, self.STEP_FIELD_MAPPING, context_override=step_context(2),
        )
        assert candidate_step1 is not None and candidate_step2 is not None

        # 2つのStep由来の候補は、そもそも異なるcontextのため競合しない
        # (detect_conflicts()の(semantic_type, context)グルーピングにより無関係)。
        report = InterpretationReport(accepted=[candidate_step1, candidate_step2])

        apply_interpretation_report(experiment, report)

        condition_keys = {p.key: p.value for p in experiment.condition_properties}
        assert condition_keys == {
            "ns1:temperature__step_1": Decimal("40"),
            "ns1:temperature__step_2": Decimal("80"),
        }

        builder = MaimlBuilder()
        xml_root = builder.build(experiment)
        xml_bytes = etree.tostring(xml_root)
        assert b"ns1:temperature__step_1" in xml_bytes
        assert b"ns1:temperature__step_2" in xml_bytes
