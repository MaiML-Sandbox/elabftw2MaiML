"""実験本文のセクション分割・分類 (`interpretation/sections.py`) のテスト
(elabftw2MaiML_section_detection_classification_design.md 「テスト項目」)。

- 見出し検出 (空行・既知alias・Markdown/太字・空行の無い本文)
- 見出しの正規化と分類 (日英・連番・コロン・先頭一致・未知見出し)
- 同じ見出しの繰り返し (washing:1, washing:2, ...) と階層path
- パイプラインへの接続 (context・競合回避・note除外・未知セクションのレポート)
- 実際の実験ノート (tests/note_cell_prep_sem.txt) を通した結果
- CLI `--section-mapping`
"""
import sys
from pathlib import Path

import pytest
from lxml import etree

import elabftw_to_maiml as cli_module
from elabftw2maiml.builder import MaimlBuilder
from elabftw2maiml.elabftw_client import ElabftwClient
from elabftw2maiml.interpretation import (
    InterpretationPipeline,
    SectionClassifier,
    SectionDetector,
    SectionMapping,
    SectionMappingError,
    SemanticMapping,
    apply_interpretation_report,
    format_interpretation_report,
    html_to_structured_text,
)
from elabftw2maiml.model import ExperimentData

from .fixtures import FIXTURES, make_client

SECTION_YAML = (
    Path(__file__).parent.parent
    / "elabftw2maiml" / "interpretation" / "section_mappings" / "default_sections.yaml"
)
NOTE = (Path(__file__).parent / "note_cell_prep_sem.txt").read_text(encoding="utf-8")

MAPPING = SectionMapping.from_yaml_file(str(SECTION_YAML))
DETECTOR = SectionDetector(MAPPING)
CLASSIFIER = SectionClassifier(MAPPING)


def classified(text, detector=DETECTOR):
    return CLASSIFIER.classify_all(detector.split(text))


# ---------------------------------------------------------------------------
# HTML -> 構造付きテキスト
# ---------------------------------------------------------------------------

class TestHtmlToStructuredText:
    def test_paragraphs_become_lines_and_empty_paragraph_becomes_blank_line(self):
        html = "<p>試料</p><p>Ax2</p><p>&nbsp;</p><p>前固定</p><p>20 min</p>"
        assert html_to_structured_text(html) == "試料\nAx2\n\n前固定\n20 min"

    def test_headings_become_markdown(self):
        html = "<h1>SEM観察</h1><p>text</p><h2>観察条件</h2><p>HV 5 kV</p>"
        text = html_to_structured_text(html)
        assert "# SEM観察" in text and "## 観察条件" in text

    def test_bold_only_paragraph_becomes_bold_line(self):
        assert "**前固定**" in html_to_structured_text("<p><strong>前固定</strong></p><p>x</p>")

    def test_br_and_entities(self):
        assert html_to_structured_text("a&amp;b<br>c") == "a&b\nc"

    def test_plain_text_keeps_line_structure(self):
        assert html_to_structured_text("試料\nAx2\n\n前固定") == "試料\nAx2\n\n前固定"

    def test_empty(self):
        assert html_to_structured_text(None) is None
        assert html_to_structured_text("<p>&nbsp;</p>") is None


# ---------------------------------------------------------------------------
# 見出し検出
# ---------------------------------------------------------------------------

class TestDetection:
    def test_two_sections_from_design_example(self):
        sections = DETECTOR.split("試料\nAx2\n\n前固定\n20 min @4℃")
        assert [s.title for s in sections] == ["試料", "前固定"]
        assert sections[0].body == "Ax2"
        assert sections[1].body == "20 min @4℃"
        assert [s.key for s in sections] == ["section:1", "section:2"]

    def test_headings_detected_without_blank_lines(self):
        titles = [s.title for s in DETECTOR.split(NOTE)]
        assert titles[:5] == ["細胞培養 in KK2", "前固定", "洗浄", "除膜", "洗浄"]
        assert len(titles) == 13

    def test_short_value_lines_are_not_headings_without_blank_lines(self):
        titles = [s.title for s in DETECTOR.split(NOTE)]
        for line in ("KK2 on ice", "milliQ on ice", "incubate: 20 min @4℃", "KK2 ＠RT"):
            assert line not in titles

    def test_unknown_short_heading_between_blank_lines(self):
        sections = DETECTOR.split("試料\nAx2\n\n細胞の準備\n播種した\n\n前固定\n20 min")
        assert [s.title for s in sections] == ["試料", "細胞の準備", "前固定"]

    def test_trailing_colon_is_dropped_from_title(self):
        (s,) = DETECTOR.split("前固定：\n20 min")
        assert s.title == "前固定" and s.body == "20 min"

    def test_text_before_first_heading_is_kept_as_preamble(self):
        sections = DETECTOR.split("実験の目的を書く。\n\n前固定\n20 min")
        assert sections[0].is_preamble and sections[0].title == ""
        assert sections[0].body == "実験の目的を書く。"
        assert sections[1].title == "前固定"

    def test_text_without_headings_is_a_single_preamble(self):
        (s,) = DETECTOR.split("加速電圧 5 kV で観察した。")
        assert s.is_preamble and s.body == "加速電圧 5 kV で観察した。"

    def test_markdown_levels_and_parent(self):
        text = "# SEM観察\n\n## 試料準備\nAx2\n\n## 観察条件\nHV 5 kV\n\n# 結果\nok"
        s = DETECTOR.split(text)
        assert [(x.title, x.level, x.parent_id) for x in s] == [
            ("SEM観察", 1, None),
            ("試料準備", 2, "section:1"),
            ("観察条件", 2, "section:1"),
            ("結果", 1, None),
        ]

    def test_bold_line_is_heading(self):
        sections = DETECTOR.split("**前処理**\nAx2 を播種\nKK2 on ice")
        assert [s.title for s in sections] == ["前処理"]

    def test_empty_text(self):
        assert DETECTOR.split(None) == []
        assert DETECTOR.split("  \n ") == []


# ---------------------------------------------------------------------------
# 分類・正規化
# ---------------------------------------------------------------------------

class TestClassification:
    @pytest.mark.parametrize("heading, expected", [
        ("試料", "material"), ("Sample", "material"), ("前固定", "fixation"),
        ("前 固定", "fixation"), ("前固定：", "fixation"), ("pre-fixation", "fixation"),
        ("Pre Fixation", "fixation"), ("固定1", "fixation"), ("Fixation 2", "fixation"),
        ("洗浄(3)", "washing"), ("Washing", "washing"), ("SEM観察", "observation"),
        ("ＳＥＭ観察", "observation"), ("考察", "note"),
    ])
    def test_lookup_heading(self, heading, expected):
        assert MAPPING.lookup_heading(heading) == expected

    def test_prefix_match_for_natural_headings(self):
        assert MAPPING.match_heading("細胞培養 in KK2") == ("culture", "prefix")
        assert MAPPING.match_heading("洗浄しました") is None  # 空白/括弧が無い続きは対象外

    def test_unknown_heading_is_kept_as_unknown(self):
        out = classified("試料\nAx2\n\n細胞の準備\n播種した\n\n前固定\n20 min")
        unknown = [s for s in out if s.section_type == "unknown"]
        assert [s.title for s in unknown] == ["細胞の準備"]
        assert unknown[0].body == "播種した"          # 本文を捨てない
        assert unknown[0].context == "section:unknown:1"

    def test_original_title_is_preserved(self):
        (s,) = classified("Pre Fixation\n20 min")
        assert s.title == "Pre Fixation" and s.section_type == "fixation"

    def test_repeated_headings_get_distinct_contexts(self):
        out = classified("洗浄\nKK2 on ice\n洗浄\nmilliQ on ice\n洗浄\nKK2")
        assert [s.context for s in out] == [
            "section:washing:1", "section:washing:2", "section:washing:3"]

    def test_english_headings(self):
        out = classified("Sample\nAx2\n\nFixation\n20 min\n\nWashing\nKK2")
        assert [s.section_type for s in out] == ["material", "fixation", "washing"]

    def test_hierarchical_context_path(self):
        out = classified("# SEM観察\n\n## 観察\nHV 5 kV\n\n## 観察\nHV 10 kV")
        assert [s.context for s in out] == [
            "section:observation:1",
            "section:observation:1/observation:2",
            "section:observation:1/observation:3",
        ]

    def test_preamble_keeps_experiment_context(self):
        out = classified("目的　メモ。\n\n前固定\n20 min")
        assert out[0].context == "experiment"

    def test_classify_single_sets_only_type(self):
        s = DETECTOR.split("前固定\n20 min")[0]
        c = CLASSIFIER.classify(s)
        assert c.section_type == "fixation" and c.context is None


class TestSectionMappingValidation:
    def _load(self, **types):
        return SectionMapping.from_dict({"section_types": types})

    def test_bundled_yaml_loads(self):
        assert "fixation" in MAPPING.rules

    def test_rejects_alias_shared_by_two_types(self):
        with pytest.raises(SectionMappingError, match="複数の section_type"):
            self._load(a={"headings": ["固定"]}, b={"headings": ["固 定"]})

    def test_rejects_reserved_unknown(self):
        with pytest.raises(SectionMappingError, match="予約語"):
            self._load(unknown={"headings": ["x"]})

    def test_rejects_empty_headings(self):
        with pytest.raises(SectionMappingError, match="headings"):
            self._load(a={"headings": []})

    def test_rejects_unknown_key_and_bad_name(self):
        with pytest.raises(SectionMappingError, match="未知のキー"):
            self._load(a={"headings": ["x"], "heading": ["y"]})
        with pytest.raises(SectionMappingError, match="不正"):
            self._load(**{"1bad": {"headings": ["x"]}})

    def test_rejects_wrong_version(self):
        with pytest.raises(SectionMappingError, match="version"):
            SectionMapping.from_dict({"version": 2, "section_types": {"a": {"headings": ["x"]}}})


# ---------------------------------------------------------------------------
# パイプライン接続
# ---------------------------------------------------------------------------

_SEMANTIC = SemanticMapping.from_dict({
    "version": 2,
    "semantic_types": {
        "temperature": {"role": "condition", "target": "conditions",
                        "unit": "degC", "data_type": "number"},
        "duration": {"role": "condition", "target": "conditions",
                     "unit": "min", "data_type": "number"},
    },
}).to_field_mapping()


def _pipeline(**kw):
    return InterpretationPipeline(
        confidence_threshold=0.95, field_mapping=_SEMANTIC,
        section_detector=DETECTOR, section_classifier=CLASSIFIER, **kw)


def _exp(body):
    return ExperimentData(elab_id=1, title="t", date=None, body_text=body)


class TestPipelineIntegration:
    def test_semantic_extraction_gets_section_context(self):
        report = _pipeline().interpret_experiment(_exp("前固定\n1% PFA\nincubate: 20 min @4℃"))
        got = {(c.semantic_type, c.value, c.unit, c.context) for c in report.accepted}
        assert got == {
            ("duration", 20, "min", "section:fixation:1"),
            ("temperature", 4, "degC", "section:fixation:1"),
        }

    def test_different_sections_do_not_conflict(self):
        text = "前固定\n20 min\n\n除膜\n30 min\n\n脱水\n5 min"
        report = _pipeline().interpret_experiment(_exp(text))
        assert report.conflicts == []
        assert {(c.value, c.context) for c in report.accepted} == {
            (20, "section:fixation:1"), (30, "section:permeabilization:1"),
            (5, "section:dehydration:1")}

    def test_same_values_without_sections_conflict_as_before(self):
        # セクション分割を使わない従来の挙動 (回帰): 同じcontext="experiment"に集まって競合する
        pipeline = InterpretationPipeline(confidence_threshold=0.95, field_mapping=_SEMANTIC)
        report = pipeline.interpret_experiment(_exp("前固定\n20 min\n\n除膜\n30 min"))
        assert len(report.conflicts) == 1
        assert report.accepted == []

    def test_note_section_is_extracted_but_not_auto_applied(self):
        text = "前固定\n20 min\n\n考察\nもう少し短くてもよい。10 minでもよかった"
        report = _pipeline().interpret_experiment(_exp(text))
        assert {c.value for c in report.accepted} == {20}
        (c,) = [c for c in report.unclassified if c.value == 10]
        assert c.context == "section:note:1" and c.role is None and c.target is None
        assert "note" in c.reason

    def test_excluded_section_types_is_configurable(self):
        text = "考察\n10 min でもよかった"
        report = _pipeline(excluded_section_types=()).interpret_experiment(_exp(text))
        assert {c.value for c in report.accepted} == {10}

    def test_detector_and_classifier_must_be_given_together(self):
        with pytest.raises(ValueError):
            InterpretationPipeline(section_detector=DETECTOR)
        with pytest.raises(ValueError):
            InterpretationPipeline(section_classifier=CLASSIFIER)

    def test_body_text_argument_overrides_experiment_body(self):
        exp = _exp("前固定 20 min 除膜 30 min")  # 改行が失われた本文
        report = _pipeline().interpret_experiment(exp, body_text="前固定\n20 min\n\n除膜\n30 min")
        assert {c.context for c in report.accepted} == {
            "section:fixation:1", "section:permeabilization:1"}

    def test_step_body_keeps_step_context(self):
        from elabftw2maiml.model import Step
        exp = ExperimentData(elab_id=1, title="t", date=None, body_text=None,
                             steps=[Step(elab_id=7, title="s", body="20 min")])
        report = _pipeline().interpret_experiment(exp)
        assert [c.context for c in report.accepted] == ["step:7"]

    def test_report_lists_sections_and_unknown_sections(self):
        text = "試料\nAx2\n\n細胞の準備\n播種した。20 min\n\n前固定\n20 min"
        report = _pipeline().interpret_experiment(_exp(text))
        out = format_interpretation_report(report)
        assert "実験本文のセクション" in out
        assert "未知のセクション" in out and "細胞の準備" in out
        # 未知セクションの本文も解釈されている (捨てられない)
        assert any(c.context == "section:unknown:1" for c in report.candidates)

    def test_report_has_no_section_block_when_disabled(self):
        pipeline = InterpretationPipeline(confidence_threshold=0.95)
        out = format_interpretation_report(pipeline.interpret_experiment(_exp("20 min")))
        assert "セクション" not in out


# ---------------------------------------------------------------------------
# 実際の実験ノート
# ---------------------------------------------------------------------------

class TestRealNote:
    def _report(self):
        return _pipeline().interpret_experiment(_exp(NOTE))

    def test_section_types(self):
        types = [s.section_type for s in classified(NOTE)]
        assert types == [
            "culture", "fixation", "washing", "permeabilization", "washing",
            "fixation", "washing", "fixation", "washing", "dehydration",
            "substitution", "freezing", "drying"]

    def test_values_are_attributed_to_their_own_section(self):
        by_ctx = {}
        for c in self._report().candidates:
            by_ctx.setdefault(c.context, []).append((c.semantic_type, c.value, c.unit))
        assert by_ctx["section:fixation:1"] == [("duration", 20, "min"), ("temperature", 4, "degC")]
        assert by_ctx["section:permeabilization:1"] == [
            ("duration", 30, "min"), ("duration", 30, "min")]
        assert by_ctx["section:fixation:2"] == [("duration", 20, "min")]
        assert by_ctx["section:fixation:3"] == [("duration", 5, "min")]

    def test_conflicts_are_limited_to_sections_with_several_different_values(self):
        report = self._report()
        # 脱水: 5/10/10/10 min が同一セクション内に混在する。同じ値が並ぶ除膜 (30,30) は
        # 競合にならない。複数条件の分割 (Statement) は未実装のため、脱水は競合として残る
        assert [c.context for c in report.conflicts] == ["section:dehydration:1"]
        accepted_contexts = {c.context for c in report.accepted}
        assert {"section:fixation:1", "section:permeabilization:1",
                "section:fixation:2", "section:fixation:3"} <= accepted_contexts

    def test_culture_hours_are_not_completed_because_of_unit_mismatch(self):
        culture = [c for c in self._report().candidates if c.context == "section:culture:1"]
        assert [(c.semantic_type, c.value, c.unit, c.role) for c in culture] == [
            ("duration", 11, "h", None)]

    def test_apply_and_build_well_formed_xml(self):
        exp = _exp(NOTE)
        report = self._report()
        logs = apply_interpretation_report(exp, report, ns_prefix="ns1")
        keys = {c.key for c in exp.conditions}
        assert "condition:section:fixation:1" in keys
        assert "condition:section:permeabilization:1" in keys
        assert any("[反映]" in line for line in logs)
        xml = MaimlBuilder(ns_prefix="ns1", ns_uri="https://example.org/maiml/mylab",
                           elab_host="elab.example.org").to_bytes(exp)
        etree.fromstring(xml)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_FIELD_MAPPING_YAML = """
version: 2
semantic_types:
  temperature:
    role: condition
    target: conditions
    unit: degC
    data_type: number
  duration:
    role: condition
    target: conditions
    unit: min
    data_type: number
"""


def _run_cli(tmp_path, monkeypatch, extra_args, body_html):
    fake_experiment, raw_json, raw_items = FIXTURES["fixture_a"]()
    raw_json = dict(raw_json, body=body_html)
    client = make_client(ElabftwClient, fake_experiment, raw_json, raw_items)
    monkeypatch.setattr(cli_module, "ElabftwClient", lambda **kwargs: client)
    mapping = tmp_path / "mapping.yaml"
    mapping.write_text(_FIELD_MAPPING_YAML, encoding="utf-8")
    out = tmp_path / "out.maiml"
    monkeypatch.setattr(sys, "argv", [
        "elabftw_to_maiml.py", "--experiment-id", str(fake_experiment.id),
        "--host", "https://elab.example.org/api/v2", "--api-key", "dummy",
        "--output", str(out), "--field-mapping", str(mapping),
        "--confidence-threshold", "0.95", *extra_args])
    return cli_module.main(), out


def test_cli_section_mapping_uses_structured_body(tmp_path, monkeypatch, capsys):
    html = ("<p>前固定</p><p>incubate: 20 min @4℃</p><p>&nbsp;</p>"
            "<p>除膜</p><p>@RT 30 min</p>")
    code, out = _run_cli(tmp_path, monkeypatch, ["--section-mapping", str(SECTION_YAML)], html)
    assert code == 0 and out.read_bytes().startswith(b"<?xml")
    text = capsys.readouterr().out
    assert "section:fixation:1" in text and "section:permeabilization:1" in text
    assert "競合: 0件" in text


def test_cli_without_section_mapping_keeps_single_experiment_context(tmp_path, monkeypatch, capsys):
    html = "<p>前固定</p><p>20 min</p><p>&nbsp;</p><p>除膜</p><p>30 min</p>"
    code, _out = _run_cli(tmp_path, monkeypatch, [], html)
    assert code == 0
    text = capsys.readouterr().out
    assert "section:" not in text


def test_cli_section_mapping_requires_field_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "elabftw_to_maiml.py", "--experiment-id", "1", "--host", "https://h/api/v2",
        "--api-key", "k", "--section-mapping", str(SECTION_YAML)])
    with pytest.raises(SystemExit):
        cli_module.main()
