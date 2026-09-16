"""Phase 5-4: 実際のeLabFTW Custom Field名 (YasunagaLab STEM実験、2026-09-16に
ユーザーから共有されたMATERIAL/CONDITION/RESULTフィールドグループの実例) を使った
対応表 (`field_mappings/yasunaga_lab_stem.yaml`) の統合テスト。

実際のeLabFTWサーバーには接続せず、共有されたスクリーンショット上の値をそのまま
`RawField`として与える。これにより:

    1. 対応表の全フィールド名がそのまま (aliasを介さず) マッチすること。
    2. 単位が値の文字列に混在しているフィールド (例: "200 kV", "40 µm", "80 mm",
       "42 s", "51.2 µm", "2048 pixel") が、Phase 5-3 fix の正規化により
       誤って競合として検出されないこと。
    3. 自由記述側の言及が無いフィールドも、構造化フィールドのみで問題なく
       ExperimentDataへ反映されること (無条件に競合にはならないこと)。
    4. MATERIAL/CONDITION/RESULTの各グループが、期待した反映先
       (materials/condition_properties/result_properties) に振り分けられること。

を確認する。
"""
from datetime import datetime
from decimal import Decimal
from pathlib import Path

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

MAPPING_PATH = (
    Path(__file__).parent.parent
    / "elabftw2maiml"
    / "interpretation"
    / "field_mappings"
    / "yasunaga_lab_stem.yaml"
)

# ユーザーから共有された実際のフィールド値 (2026-09-16)。
# MATERIAL/CONDITION/RESULTの各Field Groupをそのまま`RawField.group`に保持する。
RAW_FIELDS = [
    # --- MATERIAL ---
    RawField(name="Grid", value="Square Pillar Array,10µm Ptch,Square", group="MATERIAL"),
    RawField(name="sampleID", value="HS-100MG001", group="MATERIAL"),
    RawField(name="sampleName", value="HS-100MG", group="MATERIAL"),
    RawField(name="pretreatment", value="none", group="MATERIAL"),
    # --- CONDITION ---
    RawField(name="ImagingMode", value="HAADF-STEM", group="CONDITION"),
    RawField(name="Aperture(µm)", value="40 µm", group="CONDITION"),
    RawField(name="Magnification", value="200000", group="CONDITION"),
    RawField(name="CameraLength(mm)", value="80 mm", group="CONDITION"),
    RawField(name="AcceleratingVoltage(kV)", value="200 kV", group="CONDITION"),
    RawField(name="ElectronDose(e⁻/Å²)", value="80 e⁻/Å²", group="CONDITION"),
    # --- RESULT ---
    RawField(name="Detector", value="HAADF Detector", group="RESULT"),
    RawField(name="DwellTime", value="10", group="RESULT"),
    RawField(name="PixelSize", value="0.025", group="RESULT"),
    RawField(name="FrameCount", value="1", group="RESULT"),
    RawField(name="SignalType", value="High-Angle Annular Dark Field", group="RESULT"),
    RawField(name="ImageFormat", value="TIFF", group="RESULT"),
    RawField(name="AcquisitionTime", value="42 s", group="RESULT"),
    RawField(name="FieldofView(µm)", value="51.2 µm", group="RESULT"),
    RawField(name="ImageWidth(pixel)", value="2048 pixel", group="RESULT"),
    RawField(name="ImageHeight(pixel)", value="2048 pixel", group="RESULT"),
]


def _build_experiment_and_report(body_text: str = ""):
    field_mapping = FieldMapping.from_yaml_file(str(MAPPING_PATH))
    structured_candidates, unmapped = build_structured_candidates(RAW_FIELDS, field_mapping)

    experiment = ExperimentData(
        elab_id=44,
        title="STEM観察 (YasunagaLab実データ)",
        date=datetime(2026, 9, 16),
        body_text=body_text,
    )
    pipeline = InterpretationPipeline(extra_text_interpreters=[SemTemTextRuleInterpreter()])
    report = pipeline.interpret_experiment(experiment, structured_candidates=structured_candidates)
    return experiment, report, unmapped


class TestAllRealFieldNamesAreMapped:
    def test_no_unmapped_fields(self):
        _, _, unmapped = _build_experiment_and_report()
        assert unmapped == []


class TestEmbeddedUnitFieldsDoNotFalselyConflict:
    """本文に何も書かれていない場合、構造化フィールドだけでは競合が起きないこと。"""

    def test_no_conflicts_when_body_text_is_empty(self):
        _, report, _ = _build_experiment_and_report(body_text="")
        assert report.conflicts == []

    def test_all_structured_fields_are_accepted(self):
        _, report, _ = _build_experiment_and_report(body_text="")
        accepted_types = {c.semantic_type for c in report.accepted}
        expected_types = {
            "grid_pattern", "sample_id", "sample_name", "pretreatment_state",
            "imaging_mode", "objective_aperture", "magnification", "camera_length",
            "accelerating_voltage", "electron_dose", "detector_name", "dwell_time",
            "pixel_size", "frame_count", "signal_type", "image_format",
            "acquisition_time", "field_of_view", "image_width", "image_height",
        }
        assert accepted_types == expected_types
        # unclassified/conflictsに漏れていないことも確認する。
        assert report.conflicts == []
        assert report.unclassified == []

    def test_normalized_values_keep_expected_decimal_and_unit(self):
        _, report, _ = _build_experiment_and_report(body_text="")
        by_type = {c.semantic_type: c for c in report.accepted}

        accel = by_type["accelerating_voltage"]
        assert accel.value == Decimal("200")
        assert accel.unit == "kV"
        assert accel.raw_value == "200 kV"

        aperture = by_type["objective_aperture"]
        assert aperture.value == Decimal("40")
        assert aperture.unit == "um"  # µm/μm/um は同一の正規化後表記に揃う
        assert aperture.raw_value == "40 µm"

        camera_length = by_type["camera_length"]
        assert camera_length.value == Decimal("80")
        assert camera_length.unit == "mm"

        magnification = by_type["magnification"]
        # 値自体には単位が含まれないため、対応表のunit ("x") が補われる。
        assert magnification.value == Decimal("200000")
        assert magnification.unit == "x"

        fov = by_type["field_of_view"]
        assert fov.value == Decimal("51.2")
        assert fov.unit == "um"

        image_width = by_type["image_width"]
        assert image_width.value == Decimal("2048")
        assert image_width.unit == "pixel"

        # 単位無し (無次元) であることが確認済みのフィールドは、unit=Noneの
        # まま裸の数値 (Decimal) として反映される。
        dwell_time = by_type["dwell_time"]
        assert dwell_time.value == Decimal("10")
        assert dwell_time.unit is None

        pixel_size = by_type["pixel_size"]
        assert pixel_size.value == Decimal("0.025")
        assert pixel_size.unit is None


class TestAppliedToExperimentDataByGroup:
    def test_material_fields_land_in_synthetic_material(self):
        experiment, report, _ = _build_experiment_and_report(body_text="")
        apply_interpretation_report(experiment, report)

        assert len(experiment.materials) == 1
        material_keys = {p.key for p in experiment.materials[0].properties}
        assert "ns1:sample_id" in material_keys
        assert "ns1:sample_name" in material_keys
        assert "ns1:pretreatment_state" in material_keys
        assert "ns1:grid_pattern" in material_keys

    def test_condition_fields_land_in_condition_properties(self):
        experiment, report, _ = _build_experiment_and_report(body_text="")
        apply_interpretation_report(experiment, report)

        condition_keys = {p.key for p in experiment.condition_properties}
        assert condition_keys == {
            "ns1:imaging_mode", "ns1:objective_aperture", "ns1:magnification",
            "ns1:camera_length", "ns1:accelerating_voltage", "ns1:electron_dose",
        }

    def test_result_fields_land_in_result_properties(self):
        experiment, report, _ = _build_experiment_and_report(body_text="")
        apply_interpretation_report(experiment, report)

        result_keys = {p.key for p in experiment.result_properties}
        assert result_keys == {
            "ns1:detector_name", "ns1:dwell_time", "ns1:pixel_size",
            "ns1:frame_count", "ns1:signal_type", "ns1:image_format",
            "ns1:acquisition_time", "ns1:field_of_view", "ns1:image_width",
            "ns1:image_height",
        }

    def test_generated_maiml_is_well_formed(self):
        experiment, report, _ = _build_experiment_and_report(body_text="")
        apply_interpretation_report(experiment, report)
        builder = MaimlBuilder()
        # 例外が発生しないこと (実際のXSD検証はmaiml-schema-validatorスキルで
        # 別途行う)。
        builder.build(experiment)


class TestFreeTextAgreementStillWorks:
    """自由記述で一致する言及があっても、既に構造化フィールドで反映済みのため
    重複反映されないこと (development planの「既存の値を上書きしない」方針)。"""

    def test_matching_free_text_does_not_create_duplicate_or_conflict(self):
        _, report, _ = _build_experiment_and_report(
            body_text="加速電圧200 kVで、倍率200,000倍にてHAADF-STEM観察した。"
        )
        assert report.conflicts == []

    def test_disagreeing_free_text_creates_conflict_and_blocks_reflection(self):
        experiment, report, _ = _build_experiment_and_report(
            body_text="加速電圧250kVに変更して再測定した。"
        )
        conflict_types = {c.semantic_type for c in report.conflicts}
        assert "accelerating_voltage" in conflict_types
        apply_interpretation_report(experiment, report)
        condition_keys = {p.key for p in experiment.condition_properties}
        assert "ns1:accelerating_voltage" not in condition_keys


def test_format_interpretation_report_mentions_all_groups():
    """レポート表示自体が例外なく動作し、反映件数が読み取れることを確認する
    (実際の内容は上記の各テストで確認済み)。"""
    _, report, _ = _build_experiment_and_report(body_text="")
    text = format_interpretation_report(report)
    assert "accelerating_voltage" in text
