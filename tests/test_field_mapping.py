"""`elabftw2maiml/interpretation/field_mapping.py` (Phase 5-2) の単体テスト。

SEM_TEM_field_mapping_example.md の内容を素材に、以下を確認する:

- `FieldMapping.from_dict` / `from_yaml_file` による対応表の読み込み
- フィールド名・aliasの両方での `lookup()`
- `candidate_from_field()` が role/semantic_type/unit/context/targetを正しく
  組み立てること、および `context_override`/`role_override` の優先順位
  (design doc 7節: SEM/TEM共通フィールドをGroupで区別するケース)
- `build_structured_candidates()` が未定義フィールドを取り零さず
  `unmapped_fields` として返すこと (design doc 15節-9)
"""
import os
from decimal import Decimal

import pytest

from elabftw2maiml.interpretation.field_mapping import (
    RawField,
    FieldRule,
    FieldMapping,
    candidate_from_field,
    build_structured_candidates,
)

EXAMPLE_YAML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "elabftw2maiml",
    "interpretation",
    "field_mappings",
    "sem_tem_example.yaml",
)


def _simple_mapping() -> FieldMapping:
    return FieldMapping.from_dict(
        {
            "fields": {
                "加速電圧": {
                    "aliases": ["Acceleration Voltage", "HV"],
                    "role": "condition",
                    "semantic_type": "accelerating_voltage",
                    "unit": "kV",
                    "target": "condition_properties",
                    "required": True,
                },
                "試料ID": {
                    "role": "material",
                    "semantic_type": "sample_id",
                    "context": "experiment",
                    "target": "materials",
                },
            }
        }
    )


class TestFieldMappingFromDict:
    def test_builds_rule_with_all_attributes(self):
        mapping = _simple_mapping()
        rule = mapping.lookup("加速電圧")
        assert rule is not None
        assert rule.semantic_type == "accelerating_voltage"
        assert rule.role == "condition"
        assert rule.unit == "kV"
        assert rule.target == "condition_properties"
        assert rule.required is True

    def test_lookup_by_alias(self):
        mapping = _simple_mapping()
        by_alias = mapping.lookup("HV")
        by_name = mapping.lookup("加速電圧")
        assert by_alias is by_name

    def test_lookup_unknown_field_returns_none(self):
        mapping = _simple_mapping()
        assert mapping.lookup("存在しないフィールド") is None

    def test_lookup_strips_whitespace(self):
        mapping = _simple_mapping()
        assert mapping.lookup("  加速電圧  ") is not None

    def test_contains(self):
        mapping = _simple_mapping()
        assert "加速電圧" in mapping
        assert "HV" in mapping
        assert "存在しないフィールド" not in mapping

    def test_len_counts_names_and_aliases(self):
        mapping = _simple_mapping()
        # 加速電圧 + Acceleration Voltage + HV + 試料ID = 4 keys
        assert len(mapping) == 4

    def test_missing_semantic_type_raises(self):
        with pytest.raises(KeyError):
            FieldMapping.from_dict({"fields": {"倍率": {"role": "condition"}}})

    def test_empty_fields_dict(self):
        mapping = FieldMapping.from_dict({})
        assert len(mapping) == 0
        assert mapping.lookup("何でも") is None


class TestFieldMappingFromYamlFile:
    def test_loads_real_sem_tem_example(self):
        mapping = FieldMapping.from_yaml_file(EXAMPLE_YAML_PATH)
        # SEM MVP (10) + TEM MVP (11) - 試料ID/装置名は共通なので重複を除くと
        # 一意フィールド数は17 (3節共通2 + SEM専有6 + TEM専有5 + 共通4 [加速電圧・
        # 観察モード・倍率・粒径])。正確な件数よりも、代表的なフィールドが
        # 正しく引けることを確認する。
        assert "試料ID" in mapping
        assert "加速電圧" in mapping
        assert "TEMグリッド" in mapping
        assert "SEM画像" in mapping
        assert "TEM画像" in mapping

    def test_shared_fields_have_no_fixed_context(self):
        mapping = FieldMapping.from_yaml_file(EXAMPLE_YAML_PATH)
        rule = mapping.lookup("加速電圧")
        assert rule.context is None
        assert rule.semantic_type == "accelerating_voltage"
        assert rule.unit == "kV"

    def test_sem_specific_field_has_fixed_context(self):
        mapping = FieldMapping.from_yaml_file(EXAMPLE_YAML_PATH)
        rule = mapping.lookup("検出器")
        assert rule.context == "sem_acquisition"

    def test_alias_lookup_on_real_file(self):
        mapping = FieldMapping.from_yaml_file(EXAMPLE_YAML_PATH)
        assert mapping.lookup("Working Distance") is mapping.lookup("ワーキングディスタンス")

    def test_missing_file_raises(self):
        with pytest.raises(OSError):
            FieldMapping.from_yaml_file("/no/such/path/does_not_exist.yaml")


class TestCandidateFromField:
    def test_basic_resolution(self):
        mapping = _simple_mapping()
        raw = RawField(name="試料ID", value="S-001")
        candidate = candidate_from_field(raw, mapping)
        assert candidate is not None
        assert candidate.semantic_type == "sample_id"
        assert candidate.role == "material"
        assert candidate.context == "experiment"
        assert candidate.target == "materials"
        assert candidate.value == "S-001"
        assert candidate.source == "custom_field"
        assert candidate.confidence == 1.0

    def test_unknown_field_returns_none(self):
        mapping = _simple_mapping()
        raw = RawField(name="未知フィールド", value=1)
        assert candidate_from_field(raw, mapping) is None

    def test_raw_unit_overrides_rule_unit(self):
        mapping = _simple_mapping()
        raw = RawField(name="加速電圧", value=15, unit="V")
        candidate = candidate_from_field(raw, mapping)
        assert candidate.unit == "V"

    def test_missing_raw_unit_falls_back_to_rule_unit(self):
        mapping = _simple_mapping()
        raw = RawField(name="加速電圧", value=15)
        candidate = candidate_from_field(raw, mapping)
        assert candidate.unit == "kV"

    def test_context_override_takes_precedence(self):
        """SEM_TEM_field_mapping_example.md 7節: 加速電圧はSEM/TEM共通のため
        対応表側のcontextはNoneだが、呼び出し側がGroup種別からcontextを
        指定できる。"""
        mapping = FieldMapping.from_yaml_file(EXAMPLE_YAML_PATH)
        raw = RawField(name="加速電圧", value=15, group="SEM_CONDITION")
        candidate = candidate_from_field(raw, mapping, context_override="sem_acquisition")
        assert candidate.context == "sem_acquisition"

        raw_tem = RawField(name="加速電圧", value=200, group="TEM_CONDITION")
        candidate_tem = candidate_from_field(raw_tem, mapping, context_override="tem_acquisition")
        assert candidate_tem.context == "tem_acquisition"

    def test_context_override_none_keeps_rule_context(self):
        mapping = _simple_mapping()
        raw = RawField(name="試料ID", value="S-001")
        candidate = candidate_from_field(raw, mapping, context_override=None)
        assert candidate.context == "experiment"

    def test_role_override_takes_precedence(self):
        mapping = _simple_mapping()
        raw = RawField(name="試料ID", value="S-001")
        candidate = candidate_from_field(raw, mapping, role_override="result")
        assert candidate.role == "result"

    def test_source_and_confidence_are_passed_through(self):
        mapping = _simple_mapping()
        raw = RawField(name="試料ID", value="S-001")
        candidate = candidate_from_field(raw, mapping, source="field_group", confidence=0.9)
        assert candidate.source == "field_group"
        assert candidate.confidence == 0.9


class TestCandidateFromFieldNormalization:
    """Phase 5-3 fix: eLabFTWのExtra Fieldsが単位混在の文字列 ("200 kV"等) で
    返ってきた場合の正規化を、`candidate_from_field()`経由で確認する
    (normalize.pyの単体テストはtest_normalize.py、ここでは統合部分のみ)。"""

    def test_value_with_embedded_unit_string_is_normalized(self):
        mapping = _simple_mapping()
        raw = RawField(name="加速電圧", value="200 kV")
        candidate = candidate_from_field(raw, mapping)
        assert candidate.value == Decimal("200")
        assert candidate.unit == "kV"
        assert candidate.role == "condition"
        assert candidate.target == "condition_properties"
        assert candidate.reason is None

    def test_value_with_embedded_unit_no_space_is_normalized(self):
        mapping = _simple_mapping()
        raw = RawField(name="加速電圧", value="200kV")
        candidate = candidate_from_field(raw, mapping)
        assert candidate.value == Decimal("200")
        assert candidate.unit == "kV"

    def test_bare_numeric_string_gets_rule_unit_filled_in(self):
        mapping = _simple_mapping()
        raw = RawField(name="加速電圧", value="200")
        candidate = candidate_from_field(raw, mapping)
        assert candidate.value == Decimal("200")
        assert candidate.unit == "kV"
        assert candidate.role == "condition"

    def test_bare_numeric_value_gets_rule_unit_filled_in(self):
        mapping = _simple_mapping()
        raw = RawField(name="加速電圧", value=200)
        candidate = candidate_from_field(raw, mapping)
        assert candidate.value == Decimal("200")
        assert candidate.unit == "kV"

    def test_dimension_mismatch_disables_auto_acceptance_but_keeps_raw_value(self):
        """"200 mA" は期待単位"kV"と次元が異なるため、自動反映を無効化
        (role/target=None) しつつ、原値をraw_valueに保持する。"""
        mapping = _simple_mapping()
        raw = RawField(name="加速電圧", value="200 mA")
        candidate = candidate_from_field(raw, mapping)
        assert candidate.role is None
        assert candidate.target is None
        assert candidate.raw_value == "200 mA"
        assert candidate.value == "200 mA"  # 正規化できないため原値のまま
        assert candidate.reason is not None
        assert "200 mA" in candidate.reason

    def test_non_numeric_value_disables_auto_acceptance_and_preserves_raw_value(self):
        """"not measured" は数値として解釈できないため、自動反映を無効化しつつ
        原値をログ (reason/raw_value/value) へ保持する。"""
        mapping = _simple_mapping()
        raw = RawField(name="加速電圧", value="not measured")
        candidate = candidate_from_field(raw, mapping)
        assert candidate.role is None
        assert candidate.target is None
        assert candidate.value == "not measured"
        assert candidate.raw_value == "not measured"
        assert candidate.reason is not None

    def test_field_with_no_expected_unit_is_not_normalized(self):
        """対応表に unit が定義されていないフィールド (文字列フィールド等) は、
        正規化を一切行わず、既存動作 (値をそのまま使う) を維持する。"""
        mapping = _simple_mapping()
        raw = RawField(name="試料ID", value="S-001")
        candidate = candidate_from_field(raw, mapping)
        assert candidate.value == "S-001"
        assert candidate.raw_value is None
        assert candidate.reason is None
        assert candidate.role == "material"

    def test_raw_unit_wins_over_rule_unit_as_expected_unit(self):
        mapping = _simple_mapping()
        raw = RawField(name="加速電圧", value="15", unit="V")
        candidate = candidate_from_field(raw, mapping)
        assert candidate.value == Decimal("15")
        assert candidate.unit == "V"
        # rule.unitは"kV"だが、raw.unitが優先されるため次元不一致にはならない。
        assert candidate.role == "condition"


class TestBuildStructuredCandidates:
    def test_all_fields_mapped(self):
        mapping = _simple_mapping()
        fields = [
            RawField(name="試料ID", value="S-001"),
            RawField(name="HV", value=15),
        ]
        candidates, unmapped = build_structured_candidates(fields, mapping)
        assert len(candidates) == 2
        assert unmapped == []

    def test_unmapped_field_is_preserved_not_dropped(self):
        """design doc 15節-9: 未知のフィールド名は黙って捨てず報告に残す。"""
        mapping = _simple_mapping()
        fields = [
            RawField(name="試料ID", value="S-001"),
            RawField(name="謎のフィールド", value="???"),
        ]
        candidates, unmapped = build_structured_candidates(fields, mapping)
        assert len(candidates) == 1
        assert len(unmapped) == 1
        assert unmapped[0].name == "謎のフィールド"

    def test_context_for_disambiguates_shared_field_by_group(self):
        """SEM_TEM_field_mapping_example.md 7節のGroupによる区別を、
        `context_for` callableで実現できることを確認する。"""
        mapping = FieldMapping.from_yaml_file(EXAMPLE_YAML_PATH)

        group_contexts = {
            "SEM_CONDITION": "sem_acquisition",
            "TEM_CONDITION": "tem_acquisition",
        }

        def context_for(raw: RawField):
            return group_contexts.get(raw.group)

        fields = [
            RawField(name="加速電圧", value=15, group="SEM_CONDITION"),
            RawField(name="加速電圧", value=200, group="TEM_CONDITION"),
        ]
        candidates, unmapped = build_structured_candidates(
            fields, mapping, context_for=context_for
        )
        assert unmapped == []
        assert len(candidates) == 2
        contexts = {c.context for c in candidates}
        assert contexts == {"sem_acquisition", "tem_acquisition"}

    def test_context_for_returning_none_keeps_rule_context(self):
        mapping = _simple_mapping()
        fields = [RawField(name="試料ID", value="S-001", group="UNKNOWN_GROUP")]

        candidates, _ = build_structured_candidates(
            fields, mapping, context_for=lambda raw: None
        )
        assert candidates[0].context == "experiment"

    def test_empty_field_list(self):
        mapping = _simple_mapping()
        candidates, unmapped = build_structured_candidates([], mapping)
        assert candidates == []
        assert unmapped == []

    def test_source_and_confidence_propagate_to_all_candidates(self):
        mapping = _simple_mapping()
        fields = [RawField(name="試料ID", value="S-001")]
        candidates, _ = build_structured_candidates(
            fields, mapping, source="field_group", confidence=0.9
        )
        assert candidates[0].source == "field_group"
        assert candidates[0].confidence == 0.9
