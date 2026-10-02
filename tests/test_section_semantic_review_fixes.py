"""`elabftw2MaiML_section_semantic_review_fixes.md` (Section/Semantic Interpretation
改修指示) 対応のテスト。

- 共通Semantic Mapping (duration/temperature) と実ファイル sem_tem_v2.yaml の補完
- 単位換算 (dimension / canonical_unit / normalize_quantity)
- StatementSplitter による同一セクション内の複数操作の分離
- `5 minx2` -> duration + repeat_count
- Operation Interpreter (自然文見出し) と、見出し中の値の扱い
- 考察文が自動反映されないこと
"""
from pathlib import Path

import pytest

from elabftw2maiml.interpretation import (
    InterpretationPipeline,
    OperationInterpreter,
    OperationMapping,
    OperationMappingError,
    SectionClassifier,
    SectionDetector,
    SectionMapping,
    SemanticMapping,
    SemanticMappingError,
    StatementSplitter,
    TextRuleInterpreter,
    dimension_of,
    load_mapping_file,
    normalize_quantity,
)
from elabftw2maiml.model import ExperimentData

ROOT = Path(__file__).parent.parent / "elabftw2maiml" / "interpretation"
SEM_TEM_V2 = ROOT / "field_mappings" / "sem_tem_v2.yaml"
COMMON = ROOT / "field_mappings" / "common_semantics.yaml"
SECTIONS = SectionMapping.from_yaml_file(str(ROOT / "section_mappings" / "default_sections.yaml"))
OPERATIONS = OperationMapping.from_yaml_file(
    str(ROOT / "operation_mappings" / "default_operations.yaml"))
NOTE = (Path(__file__).parent / "note_cell_prep_sem.txt").read_text(encoding="utf-8")

LOADED = load_mapping_file(str(SEM_TEM_V2))


def pipeline(statements=False, operations=True, threshold=0.95):
    interp = OperationInterpreter(OPERATIONS) if operations else None
    return InterpretationPipeline(
        confidence_threshold=threshold,
        field_mapping=LOADED.field_mapping,
        extra_text_interpreters=LOADED.text_interpreters,
        section_detector=SectionDetector(SECTIONS),
        section_classifier=SectionClassifier(SECTIONS, operation_interpreter=interp),
        statement_splitter=StatementSplitter() if statements else None,
    )


def run(body, **kw):
    exp = ExperimentData(elab_id=1, title="t", date=None, body_text=body)
    return pipeline(**kw).interpret_experiment(exp)


def accepted(report):
    return {(c.semantic_type, c.value, c.unit, c.context) for c in report.accepted}


# ---------------------------------------------------------------------------
# 単位換算
# ---------------------------------------------------------------------------

class TestNormalizeQuantity:
    @pytest.mark.parametrize("value, unit, dim, canon, expected", [
        (11, "h", "time", "min", (660, "min")),
        (20, "min", "time", "min", (20, "min")),
        (300, "s", "time", "min", (5, "min")),
        (0.5, "h", "time", "min", (30, "min")),
        (90, "s", "time", "min", (1.5, "min")),
        (5000, "V", "voltage", "kV", (5, "kV")),
        (5, "kV", "voltage", "V", (5000, "V")),
        (1.2, "um", "length", "nm", (1200, "nm")),
        (2.1, "angstrom", "length", "nm", (0.21, "nm")),
        (1, "cm", "length", "mm", (10, "mm")),
        (2, "nA", "current", "pA", (2000, "pA")),
        (500, "uL", "volume", "mL", (0.5, "mL")),
        (277.15, "K", "temperature", "degC", (4, "degC")),
    ])
    def test_conversions(self, value, unit, dim, canon, expected):
        assert normalize_quantity(value, unit, dim, canon) == expected

    def test_integer_results_are_int_and_floats_have_no_binary_noise(self):
        v, _ = normalize_quantity(0.1, "h", "time", "min")
        assert v == 6 and isinstance(v, int)
        v, _ = normalize_quantity(0.3, "min", "time", "s")
        assert v == 18 and isinstance(v, int)

    def test_unit_spelling_is_normalized_first(self):
        assert normalize_quantity(1.2, "µm", "length", "nm") == (1200, "nm")

    @pytest.mark.parametrize("unit, dim", [
        ("mm", "time"),        # 次元が異なる
        ("furlong", "length"),  # 未知の単位
        (None, "time"),
    ])
    def test_unconvertible_returns_none(self, unit, dim):
        assert normalize_quantity(1, unit, dim, "min" if dim == "time" else "mm") is None

    def test_unknown_dimension_or_canonical_unit_returns_none(self):
        assert normalize_quantity(1, "h", "nope", "min") is None
        assert normalize_quantity(1, "h", "time", "mm") is None

    def test_dimension_of(self):
        assert dimension_of("h") == "time" and dimension_of("kV") == "voltage"
        assert dimension_of("furlong") is None and dimension_of(None) is None


class TestSemanticRuleDimension:
    def _mk(self, **spec):
        return SemanticMapping.from_dict(
            {"version": 2, "semantic_types": {"duration": spec}})

    def test_valid(self):
        m = self._mk(dimension="time", canonical_unit="min")
        assert m.rules["duration"].standard_unit == "min"

    def test_unit_is_used_as_canonical_unit_when_omitted(self):
        assert self._mk(dimension="time", unit="min").rules["duration"].standard_unit == "min"

    def test_rejects_unknown_dimension(self):
        with pytest.raises(SemanticMappingError, match="dimension"):
            self._mk(dimension="speed", canonical_unit="min")

    def test_rejects_unit_outside_dimension(self):
        with pytest.raises(SemanticMappingError, match="単位ではありません"):
            self._mk(dimension="time", canonical_unit="mm")

    def test_rejects_canonical_unit_without_dimension(self):
        with pytest.raises(SemanticMappingError, match="dimension"):
            self._mk(canonical_unit="min")

    def test_rejects_dimension_without_unit(self):
        with pytest.raises(SemanticMappingError, match="canonical_unit"):
            self._mk(dimension="time")

    def test_rejects_conflicting_unit_and_canonical_unit(self):
        with pytest.raises(SemanticMappingError, match="食い違"):
            self._mk(dimension="time", unit="s", canonical_unit="min")


class TestInclude:
    def test_sem_tem_v2_includes_common_types(self):
        for st in ("duration", "temperature", "repeat_count"):
            assert LOADED.field_mapping.lookup_semantic_type(st) is not None

    def test_own_definition_overrides_included_one(self, tmp_path):
        (tmp_path / "c.yaml").write_text(
            "version: 2\nsemantic_types:\n  duration: {role: condition, target: conditions,"
            " dimension: time, canonical_unit: min}\n", encoding="utf-8")
        (tmp_path / "m.yaml").write_text(
            "version: 2\ninclude: [c.yaml]\nsemantic_types:\n  duration: {role: result,"
            " target: results, dimension: time, canonical_unit: s}\n", encoding="utf-8")
        rule = load_mapping_file(str(tmp_path / "m.yaml")).field_mapping.lookup_semantic_type("duration")
        assert (rule.role, rule.unit) == ("result", "s")

    def test_circular_include_is_rejected(self, tmp_path):
        (tmp_path / "a.yaml").write_text("version: 2\ninclude: [b.yaml]\nsemantic_types: {}\n", encoding="utf-8")
        (tmp_path / "b.yaml").write_text("version: 2\ninclude: [a.yaml]\nsemantic_types: {}\n", encoding="utf-8")
        with pytest.raises(SemanticMappingError, match="循環"):
            load_mapping_file(str(tmp_path / "a.yaml"))


# ---------------------------------------------------------------------------
# 実ファイル sem_tem_v2.yaml による duration / temperature の補完 (10.3, 10.4)
# ---------------------------------------------------------------------------

class TestRealSemTemV2File:
    def test_duration_and_temperature_are_completed(self):
        report = run("前固定\nincubate: 20 min @4℃")
        assert accepted(report) == {
            ("duration", 20, "min", "section:fixation:1"),
            ("temperature", 4, "degC", "section:fixation:1"),
        }
        assert all((c.role, c.target) == ("condition", "conditions") for c in report.accepted)

    @pytest.mark.parametrize("text, value", [
        ("11 h", 660), ("20 min", 20), ("300 s", 5), ("約11時間", 660),
    ])
    def test_time_values_become_the_same_semantic_type_in_minutes(self, text, value):
        report = run(f"培養\n{text}")
        (c,) = [c for c in report.accepted if c.semantic_type == "duration"]
        assert (c.value, c.unit) == (value, "min")

    def test_converted_value_keeps_original_and_reason(self):
        report = run("培養\n11 h")
        (c,) = [c for c in report.accepted if c.semantic_type == "duration"]
        assert c.raw_value == 11 and "換算" in c.reason

    def test_unconvertible_unit_keeps_semantic_type_but_is_not_applied(self):
        # TextRuleInterpreter の単位表に無い単位はそもそも抽出されないため、
        # 換算不能な単位を持つ候補を直接作って確認する
        from elabftw2maiml.interpretation import InterpretationCandidate
        p = pipeline()
        c = p._complete_from_mapping(InterpretationCandidate(
            semantic_type="duration", value=3, source="free_text_regex", unit="fortnight"))
        assert c.semantic_type == "duration" and c.role is None and c.target is None

    def test_sem_tem_values_are_converted_to_standard_units(self):
        report = run("観察\n加速電圧 5000 V、作動距離 1 cm")
        (v,) = [c for c in report.candidates if c.semantic_type == "accelerating_voltage"]
        assert (v.value, v.unit) == (5, "kV")


# ---------------------------------------------------------------------------
# StatementSplitter (10.5)
# ---------------------------------------------------------------------------

DEHYDRATION = "脱水\n30% EtOH, 5 min\n50% EtOH, 10 min\n70% EtOH, 10 min"


class TestStatementSplitter:
    def test_split_by_line(self):
        sections = SectionClassifier(SECTIONS).classify_all(
            SectionDetector(SECTIONS).split(DEHYDRATION))
        statements = StatementSplitter().split(sections[0])
        assert [s.text for s in statements] == [
            "30% EtOH, 5 min", "50% EtOH, 10 min", "70% EtOH, 10 min"]
        assert [s.context for s in statements] == [
            "section:dehydration:1/statement:1",
            "section:dehydration:1/statement:2",
            "section:dehydration:1/statement:3",
        ]

    def test_blank_lines_are_skipped(self):
        sections = SectionClassifier(SECTIONS).classify_all(
            SectionDetector(SECTIONS).split("脱水\na 5 min\n\nb 10 min"))
        assert [s.index for s in StatementSplitter().split(sections[0])] == [1, 2]

    def test_preamble_is_not_split(self):
        sections = SectionClassifier(SECTIONS).classify_all(
            SectionDetector(SECTIONS).split("説明文です。\n\n前固定\n20 min"))
        assert sections[0].title == "" and StatementSplitter().split(sections[0]) == []

    def test_without_splitter_values_in_one_section_conflict(self):
        report = run(DEHYDRATION)
        assert [c.context for c in report.conflicts] == ["section:dehydration:1"]

    def test_with_splitter_each_statement_has_its_own_value(self):
        report = run(DEHYDRATION, statements=True)
        assert report.conflicts == []
        assert accepted(report) == {
            ("duration", 5, "min", "section:dehydration:1/statement:1"),
            ("duration", 10, "min", "section:dehydration:1/statement:2"),
            ("duration", 10, "min", "section:dehydration:1/statement:3"),
        }

    def test_splitter_requires_section_detection(self):
        with pytest.raises(ValueError):
            InterpretationPipeline(statement_splitter=StatementSplitter())


# ---------------------------------------------------------------------------
# minx2 -> duration + repeat_count (10.6)
# ---------------------------------------------------------------------------

class TestRepeatCount:
    @pytest.mark.parametrize("text", ["100% EtOH @RT, 5 minx2", "5 min x 2", "5 min×2"])
    def test_generic_extractor(self, text):
        got = [(v.semantic_type, v.value, v.unit) for v in TextRuleInterpreter().extract(text)]
        assert ("duration", 5, "min") in got and ("repeat_count", 2, None) in got

    def test_plain_duration_has_no_repeat_count(self):
        got = {v.semantic_type for v in TextRuleInterpreter().extract("20 min @4℃")}
        assert "repeat_count" not in got

    def test_pipeline_accepts_duration_and_repeat_count(self):
        report = run("脱水\n100% EtOH @RT, 5 minx2", statements=True)
        ctx = "section:dehydration:1/statement:1"
        assert {("duration", 5, "min", ctx), ("repeat_count", 2, None, ctx)} <= accepted(report)

    def test_minx2_in_real_note_is_no_longer_missed(self):
        report = run(NOTE, statements=True)
        repeats = [c for c in report.accepted if c.semantic_type == "repeat_count"]
        assert [c.value for c in repeats] == [2, 2]


# ---------------------------------------------------------------------------
# Operation Interpreter (10.1, 10.2)
# ---------------------------------------------------------------------------

class TestOperationInterpreter:
    def test_natural_sentence_heading(self):
        sections = SectionClassifier(
            SECTIONS, operation_interpreter=OperationInterpreter(OPERATIONS)
        ).classify_all(SectionDetector(SECTIONS).split("鉄をSEM計測する\n加速電圧 5 kV"))
        (s,) = sections
        assert s.section_type == "observation"
        assert (s.operation, s.operation_object) == ("sem_measurement", "iron")
        assert s.title == "鉄をSEM計測する"

    def test_interpret_returns_model(self):
        op = OperationInterpreter(OPERATIONS).interpret("固定した細胞をTEMで確認")
        assert (op.operation, op.object, op.source_text) == (
            "tem_measurement", "cell", "固定した細胞をTEMで確認")
        assert op.confidence == 1.0

    def test_longest_keyword_wins(self):
        assert OperationInterpreter(OPERATIONS).interpret("SEM観察").operation == "sem_measurement"
        # 「固定」と「TEM」が両方ある見出しでは、より長いキーワード (TEM) が優先される
        assert OperationInterpreter(OPERATIONS).interpret("固定した細胞をTEMで確認").operation == "tem_measurement"

    def test_no_operation_returns_none(self):
        interp = OperationInterpreter(OPERATIONS)
        assert interp.interpret("細胞の準備") is None
        assert interp.interpret("") is None and interp.interpret(None) is None

    def test_ascii_words_need_word_boundaries(self):
        assert OperationInterpreter(OPERATIONS).interpret("Semester plan") is None

    def test_section_dictionary_wins_over_operation_hint(self):
        (s,) = SectionClassifier(
            SECTIONS, operation_interpreter=OperationInterpreter(OPERATIONS)
        ).classify_all(SectionDetector(SECTIONS).split("前固定\n20 min"))
        assert s.section_type == "fixation" and s.operation == "fixation"

    def test_without_interpreter_natural_heading_stays_unknown(self):
        (s,) = SectionClassifier(SECTIONS).classify_all(
            SectionDetector(SECTIONS).split("鉄をSEM計測する\n加速電圧 5 kV"))
        assert s.section_type == "unknown" and s.operation is None

    def test_operation_shown_in_report(self):
        from elabftw2maiml.interpretation import format_interpretation_report
        report = run("鉄をSEM計測する\n加速電圧 5 kV")
        out = format_interpretation_report(report)
        assert "operation=sem_measurement" in out and "object=iron" in out
        assert "section:observation:1" in out

    def test_operation_mapping_validation(self):
        with pytest.raises(OperationMappingError, match="keywords"):
            OperationMapping.from_dict({"operations": {"a": {"keywords": []}}})
        with pytest.raises(OperationMappingError, match="複数の operation"):
            OperationMapping.from_dict({"operations": {
                "a": {"keywords": ["x"]}, "b": {"keywords": ["X"]}}})
        with pytest.raises(OperationMappingError, match="section_type"):
            OperationMapping.from_dict({"operations": {
                "a": {"keywords": ["x"], "section_type": "unknown"}}})


class TestValuesInHeadings:
    """見出し自体に値が書かれている場合の仕様 (指示書 10.2):
    意味キーワードを伴う抽出器 (v2のpatterns) だけを見出しに適用し、
    単位だけで判定する汎用抽出器 (温度・時間など) は見出しには適用しない。"""

    def test_keyword_value_in_heading_is_extracted(self):
        report = run("## 加速電圧 5 kV\nSEMで撮影した")
        got = {(c.semantic_type, c.value, c.unit) for c in report.accepted}
        assert ("accelerating_voltage", 5, "kV") in got

    def test_generic_unit_only_value_in_heading_is_not_extracted(self):
        report = run("## 20 min\n本文")
        assert not [c for c in report.candidates if c.semantic_type == "duration"]

    def test_heading_value_and_body_value_share_the_section_context(self):
        report = run("## 加速電圧 5 kV\n加速電圧 5 kV で撮影")
        ctxs = {c.context for c in report.candidates if c.semantic_type == "accelerating_voltage"}
        assert len(ctxs) == 1 and report.conflicts == []


# ---------------------------------------------------------------------------
# 考察文 (10.7) と実際のノート全体
# ---------------------------------------------------------------------------

class TestNoteSentences:
    COMMENT = "GAの濃度、低くても良かったかも。0.05%や0.01%."

    def test_comment_in_protocol_section_is_not_applied(self):
        report = run(f"前固定\nincubate: 20 min @4℃\n{self.COMMENT}", statements=True)
        assert not [c for c in report.accepted if c.value in (0.05, 0.01)]
        assert all("0.05" not in str(c.value) for c in report.accepted)

    def test_comment_in_note_section_is_not_applied(self):
        report = run(f"考察\n{self.COMMENT}\n10 min でもよい")
        assert report.accepted == []


class TestRealNoteEndToEnd:
    def test_no_conflicts_and_expected_values(self):
        report = run(NOTE, statements=True)
        assert report.conflicts == []
        values = accepted(report)
        # 培養: 11 h -> 660 min
        assert ("duration", 660, "min", "section:culture:1/statement:4") in values
        # 前固定: 20 min と 4 degC
        assert any(v[:3] == ("temperature", 4, "degC") for v in values)
        # 脱水の複数の時間が、競合せずそれぞれ反映される
        dehydration = sorted(v[1] for v in values
                             if v[0] == "duration" and "dehydration" in v[3])
        assert dehydration == [5, 5, 10, 10, 10]


# ---------------------------------------------------------------------------
# CLI (--operation-mapping / --split-statements)
# ---------------------------------------------------------------------------

import sys  # noqa: E402

import elabftw_to_maiml as cli_module  # noqa: E402
from tests.test_sections import _run_cli  # noqa: E402

OPERATION_YAML = ROOT / "operation_mappings" / "default_operations.yaml"
SECTION_YAML = ROOT / "section_mappings" / "default_sections.yaml"


def test_cli_split_statements_avoids_conflict(tmp_path, monkeypatch, capsys):
    html = "<p>脱水</p><p>30% EtOH, 5 min</p><p>50% EtOH, 10 min</p>"
    code, _ = _run_cli(tmp_path, monkeypatch,
                       ["--section-mapping", str(SECTION_YAML), "--split-statements"], html)
    assert code == 0
    text = capsys.readouterr().out
    assert "statement:1" in text and "statement:2" in text and "競合: 0件" in text


def test_cli_operation_mapping_infers_operation(tmp_path, monkeypatch, capsys):
    html = "<p>鉄をSEM計測する</p><p>加速電圧 5 kV</p>"
    code, _ = _run_cli(tmp_path, monkeypatch,
                       ["--section-mapping", str(SECTION_YAML),
                        "--operation-mapping", str(OPERATION_YAML)], html)
    assert code == 0
    assert "operation=sem_measurement" in capsys.readouterr().out


@pytest.mark.parametrize("flag", [["--operation-mapping", str(OPERATION_YAML)], ["--split-statements"]])
def test_cli_new_options_require_section_mapping(monkeypatch, flag):
    monkeypatch.setattr(sys, "argv", [
        "elabftw_to_maiml.py", "--experiment-id", "1", "--host", "https://h/api/v2",
        "--api-key", "k", *flag])
    with pytest.raises(SystemExit):
        cli_module.main()
