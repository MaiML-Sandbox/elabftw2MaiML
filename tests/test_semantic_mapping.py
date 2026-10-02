"""Semantic Mapping (version 2 対応表) のテスト
(elabftw2MaiML_semantic_mapping_externalization_design.md 19節)。

- YAML: 読み込み・検証 (不正regex/role/target/named group)・field検索
- Custom Field / 自由記述 が同じ semantic_type -> role/target に到達すること
- confidence-threshold による自由記述候補の accepted / unclassified
- Custom Field と自由記述の一致 / conflict
- 既存の SemTemTextRuleInterpreter (Python固定パターン) との出力等価性
- version 1 YAML の後方互換
"""
from pathlib import Path

import pytest

from elabftw2maiml.interpretation import (
    ConfiguredTextRuleInterpreter,
    InterpretationPipeline,
    RawField,
    SemanticMapping,
    SemanticMappingError,
    SemTemTextRuleInterpreter,
    apply_interpretation_report,
    build_structured_candidates,
    load_mapping_file,
    step_context,
)
from elabftw2maiml.model import ExperimentData, Step

BUNDLED_V2 = (
    Path(__file__).parent.parent
    / "elabftw2maiml" / "interpretation" / "field_mappings" / "sem_tem_v2.yaml"
)
BUNDLED_V1 = BUNDLED_V2.with_name("sem_tem_example.yaml")

_VALUE = r"(?P<value>-?\d[\d,]*(?:\.\d+)?)"

BASE = {
    "version": 2,
    "semantic_types": {
        "accelerating_voltage": {
            "role": "condition",
            "target": "conditions",
            "unit": "kV",
            "data_type": "number",
            "context": "experiment",
            "fields": ["加速電圧", "HV"],
            "unit_aliases": {"kV": ["KV", "kv"]},
            "patterns": [
                r"(?:加速電圧|HV)[^\d\n]{0,6}" + _VALUE + r"\s*(?P<unit>kV|KV|kv|V)(?![A-Za-z])"
            ],
        },
        "magnification": {
            "role": "condition",
            "target": "conditions",
            "unit": "x",
            "data_type": "number",
            "fields": ["倍率"],
            "patterns": [r"倍率[^\d\n]{0,6}" + _VALUE + r"\s*[倍×xX]?"],
        },
    },
}


def _mapping(**overrides):
    import copy

    data = copy.deepcopy(BASE)
    data["semantic_types"]["accelerating_voltage"].update(overrides)
    return data


# ---------------------------------------------------------------------------
# YAML の読み込み・検証
# ---------------------------------------------------------------------------

class TestLoadingAndValidation:
    def test_valid_mapping_loads(self):
        m = SemanticMapping.from_dict(BASE)
        assert set(m.rules) == {"accelerating_voltage", "magnification"}

    def test_bundled_v2_yaml_loads(self):
        m = SemanticMapping.from_yaml_file(str(BUNDLED_V2))
        for st in (
            "accelerating_voltage", "working_distance", "magnification",
            "probe_current", "specimen_tilt", "particle_size",
            "lattice_spacing", "camera_length",
        ):
            assert m.rules[st].patterns, st

    def test_lookup_field_by_name_and_alias(self):
        m = SemanticMapping.from_dict(BASE)
        assert m.lookup_field("加速電圧").semantic_type == "accelerating_voltage"
        assert m.lookup_field(" HV ").semantic_type == "accelerating_voltage"
        assert m.lookup_field("未定義") is None

    def test_rejects_invalid_regex(self):
        with pytest.raises(SemanticMappingError, match="コンパイル"):
            SemanticMapping.from_dict(_mapping(patterns=["(?P<value>\\d+"]))

    def test_rejects_pattern_without_value_group(self):
        with pytest.raises(SemanticMappingError, match="value"):
            SemanticMapping.from_dict(_mapping(patterns=[r"HV\s*(\d+)\s*(?P<unit>kV)"]))

    def test_rejects_unknown_named_group(self):
        with pytest.raises(SemanticMappingError, match="named group"):
            SemanticMapping.from_dict(
                _mapping(patterns=[r"HV(?P<value>\d+)(?P<foo>kV)"]))

    def test_rejects_missing_unit_group_without_fixed_unit(self):
        with pytest.raises(SemanticMappingError, match="固定単位"):
            SemanticMapping.from_dict(
                _mapping(unit=None, patterns=[r"HV\s*" + _VALUE]))

    def test_rejects_pattern_matching_empty_string(self):
        with pytest.raises(SemanticMappingError, match="空文字列"):
            SemanticMapping.from_dict(
                _mapping(patterns=[r"(?:HV)?(?P<value>\d*)(?P<unit>kV)?"]))

    def test_rejects_invalid_role(self):
        with pytest.raises(SemanticMappingError, match="role"):
            SemanticMapping.from_dict(_mapping(role="conditon"))

    def test_rejects_invalid_target(self):
        # v1で使われていた旧target名も、黙って通さず明示的に拒否する
        with pytest.raises(SemanticMappingError, match="target"):
            SemanticMapping.from_dict(_mapping(target="condition_properties"))

    def test_rejects_patterns_for_non_numeric_type(self):
        with pytest.raises(SemanticMappingError, match="数値のみ"):
            SemanticMapping.from_dict(_mapping(data_type="string"))

    def test_rejects_unknown_key(self):
        with pytest.raises(SemanticMappingError, match="未知のキー"):
            SemanticMapping.from_dict(_mapping(pattern=["typo"]))

    def test_rejects_field_name_shared_by_two_types(self):
        data = _mapping()
        data["semantic_types"]["magnification"]["fields"].append("HV")
        with pytest.raises(SemanticMappingError, match="複数の semantic_type"):
            SemanticMapping.from_dict(data)

    def test_rejects_wrong_version(self):
        with pytest.raises(SemanticMappingError, match="version"):
            SemanticMapping.from_dict({"version": 1, "semantic_types": {}})


# ---------------------------------------------------------------------------
# Custom Field: fields から semantic_type -> role / target / unit
# ---------------------------------------------------------------------------

class TestCustomFieldPath:
    def test_custom_field_gets_role_target_unit(self):
        fm = SemanticMapping.from_dict(BASE).to_field_mapping()
        candidates, unmapped = build_structured_candidates(
            [RawField(name="加速電圧", value=5, unit="kV")], fm)
        assert unmapped == []
        (c,) = candidates
        assert (c.semantic_type, c.value, c.unit) == ("accelerating_voltage", 5, "kV")
        assert (c.role, c.target, c.context) == ("condition", "conditions", "experiment")
        assert c.confidence == 1.0

    def test_alias_resolves_to_same_type(self):
        fm = SemanticMapping.from_dict(BASE).to_field_mapping()
        (c,), _ = build_structured_candidates([RawField(name="HV", value="5 kV")], fm)
        assert c.semantic_type == "accelerating_voltage"
        assert c.value == 5


# ---------------------------------------------------------------------------
# 自由記述: patterns から semantic_type + role/target を付与
# ---------------------------------------------------------------------------

def _experiment_with_step(body, step_id=22):
    return ExperimentData(
        elab_id=42, title="t", date=None,
        steps=[Step(elab_id=step_id, title="観察", body=body)],
    )


class TestFreeTextPath:
    def test_extracted_value_carries_role_and_target(self):
        interp = ConfiguredTextRuleInterpreter(SemanticMapping.from_dict(BASE))
        (v,) = interp.extract("加速電圧 5 kV でSEM観察")
        assert (v.semantic_type, v.value, v.unit) == ("accelerating_voltage", 5, "kV")
        assert (v.role, v.target) == ("condition", "conditions")
        assert v.method == "regex" and v.confidence == 1.0

    def test_unit_alias_normalized(self):
        interp = ConfiguredTextRuleInterpreter(SemanticMapping.from_dict(BASE))
        (v,) = interp.extract("HV=15 KV に設定")
        assert v.unit == "kV"

    def test_fixed_unit_when_pattern_has_no_unit_group(self):
        interp = ConfiguredTextRuleInterpreter(SemanticMapping.from_dict(BASE))
        (v,) = interp.extract("倍率 50,000倍")
        assert (v.semantic_type, v.value, v.unit) == ("magnification", 50000, "x")

    def test_unit_without_keyword_is_not_extracted(self):
        interp = ConfiguredTextRuleInterpreter(SemanticMapping.from_dict(BASE))
        assert interp.extract("5 kV で観察した。10 nm、100 pA。") == []

    def test_candidate_has_step_context_and_free_text_confidence(self):
        pipeline = InterpretationPipeline(
            extra_text_interpreters=[
                ConfiguredTextRuleInterpreter(SemanticMapping.from_dict(BASE))])
        exp = _experiment_with_step("加速電圧 5 kV でSEM観察")
        candidates = pipeline.free_text_candidates(exp)
        (c,) = [c for c in candidates if c.semantic_type == "accelerating_voltage"]
        assert c.context == step_context(22) == "step:22"
        assert c.source == "free_text_regex"
        assert c.confidence == pytest.approx(0.95)
        assert (c.role, c.target) == ("condition", "conditions")

    def test_generic_interpreter_values_still_have_no_role(self):
        # 汎用 TextRuleInterpreter (温度等) には role/target は付かない (従来通り)
        pipeline = InterpretationPipeline(
            extra_text_interpreters=[
                ConfiguredTextRuleInterpreter(SemanticMapping.from_dict(BASE))])
        exp = _experiment_with_step("40℃で30分加熱した")
        cands = pipeline.free_text_candidates(exp)
        assert cands and all(c.role is None and c.target is None for c in cands)


# ---------------------------------------------------------------------------
# confidence-threshold
# ---------------------------------------------------------------------------

def _pipeline(threshold):
    return InterpretationPipeline(
        extra_text_interpreters=[
            ConfiguredTextRuleInterpreter(SemanticMapping.from_dict(BASE))],
        confidence_threshold=threshold,
    )


class TestThreshold:
    def test_threshold_1_0_free_text_not_accepted(self):
        exp = _experiment_with_step("加速電圧 5 kV でSEM観察")
        report = _pipeline(1.0).interpret_experiment(exp)
        assert report.accepted == []
        assert any(c.semantic_type == "accelerating_voltage" for c in report.unclassified)

    def test_threshold_0_95_free_text_accepted(self):
        exp = _experiment_with_step("加速電圧 5 kV でSEM観察")
        report = _pipeline(0.95).interpret_experiment(exp)
        (c,) = [c for c in report.accepted if c.semantic_type == "accelerating_voltage"]
        assert c.context == "step:22"

    def test_accepted_free_text_is_applied_to_step_condition(self):
        exp = _experiment_with_step("加速電圧 5 kV でSEM観察")
        report = _pipeline(0.95).interpret_experiment(exp)
        apply_interpretation_report(exp, report, ns_prefix="ns1")

        conds = [c for c in exp.conditions if c.key == "condition:step:22"]
        assert len(conds) == 1
        props = {p.key: p for p in conds[0].properties}
        assert props["ns1:accelerating_voltage"].value == 5


# ---------------------------------------------------------------------------
# Custom Field と自由記述の一致 / conflict
# ---------------------------------------------------------------------------

def _report_with_custom_field(body, cf_value, threshold=0.95):
    # Custom Field側は context=experiment、自由記述側は実験本文 (context=experiment)
    mapping = SemanticMapping.from_dict(BASE)
    structured, _ = build_structured_candidates(
        [RawField(name="加速電圧", value=cf_value, unit="kV")],
        mapping.to_field_mapping())
    exp = ExperimentData(elab_id=42, title="t", date=None, body_text=body)
    pipeline = InterpretationPipeline(
        extra_text_interpreters=[ConfiguredTextRuleInterpreter(mapping)],
        confidence_threshold=threshold)
    return exp, pipeline.interpret_experiment(exp, structured_candidates=structured)


class TestConflict:
    def test_agreeing_values_have_no_conflict(self):
        _exp, report = _report_with_custom_field("加速電圧 5 kV で観察", 5)
        assert report.conflicts == []
        accepted = [c for c in report.accepted if c.semantic_type == "accelerating_voltage"]
        assert {c.source for c in accepted} == {"custom_field", "free_text_regex"}

    def test_disagreeing_values_conflict_and_are_not_applied(self):
        exp, report = _report_with_custom_field("加速電圧 10 kV で観察", 5)
        assert [c.semantic_type for c in report.conflicts] == ["accelerating_voltage"]
        assert not [c for c in report.accepted if c.semantic_type == "accelerating_voltage"]
        apply_interpretation_report(exp, report, ns_prefix="ns1")
        assert all(
            p.key != "ns1:accelerating_voltage"
            for c in exp.conditions for p in c.properties)


# ---------------------------------------------------------------------------
# 既存実装との等価性・後方互換
# ---------------------------------------------------------------------------

_CORPUS = [
    "加速電圧 5 kVで観察した。",
    "HV=15 kVに設定。",
    "WD 8 mmで撮影。",
    "作動距離 10 mmとした。",
    "プローブ電流 100 pA、ビーム電流 1.5 nA、照射電流 2 µA。",
    "試料傾斜角 -30°、tilt: 15 deg、傾斜角 10度。",
    "格子縞間隔 0.35 nm、d-spacing 2.1 Å、格子縞 150 pm。",
    "粒径 25 nm、Particle size 1.2 μm、粒径 3 mm。",
    "カメラ長 200 mm、カメラ長 1.5 m。",
    "倍率 50,000、倍率50,000倍で観察、倍率 50,000×。",
    "50,000倍で観察した。20倍に希釈した。",
    "magnification: 50000 / Magnification 100000",
    "50000x で観察。x50000。",
    "5 kV で観察した。10 nm。100 pA。",
    "加速電圧 5 kV、作動距離 8 mm、倍率 20,000倍で撮影した。",
    "加速電圧\n5 kV",
    "加速電圧 5 kVA",
    "",
]


@pytest.mark.parametrize("text", _CORPUS)
def test_bundled_v2_matches_python_hardcoded_interpreter(text):
    """sem_tem_v2.yaml のpatternsが、Pythonに固定されていた
    `SemTemTextRuleInterpreter` と同じ抽出結果になること (Phase 2 の移行確認)。"""
    old = SemTemTextRuleInterpreter().extract(text)
    new = load_mapping_file(str(BUNDLED_V2)).text_interpreters[0].extract(text)
    key = lambda vs: [(v.semantic_type, v.value, v.unit, v.method, v.confidence) for v in vs]
    assert key(new) == key(old)


class TestVersionDispatch:
    def test_v1_yaml_uses_legacy_interpreter(self):
        loaded = load_mapping_file(str(BUNDLED_V1))
        assert loaded.version == 1
        assert isinstance(loaded.text_interpreters[0], SemTemTextRuleInterpreter)
        assert loaded.field_mapping.lookup("加速電圧") is not None

    def test_v2_yaml_uses_configured_interpreter(self):
        loaded = load_mapping_file(str(BUNDLED_V2))
        assert loaded.version == 2
        assert isinstance(loaded.text_interpreters[0], ConfiguredTextRuleInterpreter)
        assert loaded.field_mapping.lookup("加速電圧").semantic_type == "accelerating_voltage"

    def test_v2_field_mapping_keeps_required_and_context(self):
        loaded = load_mapping_file(str(BUNDLED_V2))
        assert "加速電圧" in loaded.field_mapping.required_rules()
        assert loaded.field_mapping.lookup("試料ID").context == "experiment"

    def test_unknown_version_rejected(self, tmp_path):
        p = tmp_path / "m.yaml"
        p.write_text("version: 3\nsemantic_types: {}\n", encoding="utf-8")
        with pytest.raises(SemanticMappingError, match="version"):
            load_mapping_file(str(p))


# ---------------------------------------------------------------------------
# semantic_type をキーにした role/target の補完 (パイプライン側)
# ---------------------------------------------------------------------------

_V2_WITH_GENERIC_TYPES = {
    "version": 2,
    "semantic_types": {
        # fields/patterns を持たず、汎用抽出 (TextRuleInterpreter) の値にrole/targetを
        # 与えるためだけの定義
        "temperature": {
            "role": "condition", "target": "conditions", "unit": "degC",
            "data_type": "number",
        },
        "duration": {
            "role": "condition", "target": "conditions", "unit": "min",
            "data_type": "number",
        },
    },
}


class TestCompletionFromMappingBySemanticType:
    def _run(self, body, mapping_dict, threshold=0.95, **kw):
        fm = SemanticMapping.from_dict(mapping_dict).to_field_mapping()
        exp = ExperimentData(elab_id=1, title="t", date=None, body_text=body)
        pipeline = InterpretationPipeline(
            confidence_threshold=threshold, field_mapping=fm, **kw)
        return exp, pipeline.interpret_experiment(exp)

    def test_generic_interpreter_value_gets_role_target_and_is_accepted(self):
        _exp, report = self._run("@4℃で保存", _V2_WITH_GENERIC_TYPES)
        (c,) = [c for c in report.accepted if c.semantic_type == "temperature"]
        assert (c.value, c.unit, c.role, c.target) == (4, "degC", "condition", "conditions")
        assert c.source == "free_text_regex"

    def test_threshold_still_gates_completed_candidates(self):
        _exp, report = self._run("@4℃で保存", _V2_WITH_GENERIC_TYPES, threshold=1.0)
        assert report.accepted == []
        (c,) = report.unclassified
        assert (c.role, c.target) == ("condition", "conditions")

    def test_without_field_mapping_nothing_is_completed(self):
        exp = ExperimentData(elab_id=1, title="t", date=None, body_text="@4℃で保存")
        report = InterpretationPipeline(confidence_threshold=0.95).interpret_experiment(exp)
        assert report.accepted == []

    def test_semantic_type_not_in_mapping_stays_unclassified(self):
        _exp, report = self._run("30 mgを秤量", _V2_WITH_GENERIC_TYPES)
        assert report.accepted == []
        assert [c.semantic_type for c in report.unclassified] == ["mass"]

    def test_unit_dimension_mismatch_is_not_completed(self):
        # 対応表は duration を min と定義。"2 h" は次元の表記が異なるため補完しない
        _exp, report = self._run("2 h 乾燥", _V2_WITH_GENERIC_TYPES)
        assert report.accepted == []
        assert [c.semantic_type for c in report.unclassified] == ["duration"]

    def test_extractor_supplied_role_is_not_overwritten(self):
        data = {
            "version": 2,
            "semantic_types": {
                "accelerating_voltage": {
                    **BASE["semantic_types"]["accelerating_voltage"],
                    "role": "condition",
                },
            },
        }
        fm = SemanticMapping.from_dict(data).to_field_mapping()
        # 抽出器 (patterns) が付けたrole/targetは、対応表の補完で変わらない
        exp = ExperimentData(elab_id=1, title="t", date=None, body_text="加速電圧 5 kV")
        interp = ConfiguredTextRuleInterpreter(SemanticMapping.from_dict(data))
        report = InterpretationPipeline(
            confidence_threshold=0.95, field_mapping=fm,
            extra_text_interpreters=[interp]).interpret_experiment(exp)
        (c,) = [c for c in report.accepted if c.semantic_type == "accelerating_voltage"]
        assert (c.role, c.target) == ("condition", "conditions")

    def test_v1_mapping_also_completes_free_text(self):
        # version 1 の対応表でも、semantic_typeが定義されていれば補完される
        loaded = load_mapping_file(str(BUNDLED_V1))
        exp = ExperimentData(elab_id=1, title="t", date=None,
                             body_text="加速電圧 5 kVで観察。")
        report = InterpretationPipeline(
            confidence_threshold=0.95, field_mapping=loaded.field_mapping,
            extra_text_interpreters=loaded.text_interpreters).interpret_experiment(exp)
        (c,) = [c for c in report.candidates if c.semantic_type == "accelerating_voltage"]
        assert (c.role, c.target) == ("condition", "conditions")
        # context は自由記述の位置 ("experiment") なので、自動反映条件を満たす
        assert c in report.accepted

    def test_semantic_only_rule_is_not_a_custom_field_name(self):
        fm = SemanticMapping.from_dict(_V2_WITH_GENERIC_TYPES).to_field_mapping()
        assert fm.lookup("temperature") is None
        assert fm.lookup_semantic_type("temperature").role == "condition"
