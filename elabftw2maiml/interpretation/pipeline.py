"""
Phase 5-1: 汎用接続基盤 (elabftw2MaiML_phase5_design.md 6〜10節・13節)。

`StructuredRuleInterpreter`・`TextRuleInterpreter`・`candidate_from_extracted_value()`・
`detect_conflicts()` はこれまで単体コンポーネントとして実装されていた。このモジュールは
それらを実際の `ExperimentData` に対して呼び出し、`InterpretationReport` としてまとめる
「接続基盤」を提供する。

設計方針 (elabftw2MaiML_phase5_design.md 6節) により、eLabFTW APIとの通信・役割判定は
`elabftw_client.py`/`interpretation/structured.py` の責務のままとし、このモジュールは
「候補の収集・統合・競合検出・仕分け」のみを担当する。実際のCustom Fieldをどの
semantic_type/role/MaiML出力先に対応付けるかという設定 (elabftw2MaiML_phase5_design.md
5節の `FIELD_SEMANTIC_MAPPING` に相当) は、実運用でのフィールド命名が確定してから
Phase 5-2で導入する。そのため `interpret_experiment()` は、構造化フィールドから
あらかじめ生成された `InterpretationCandidate` のリストを外部から受け取る形にして
おり (`structured_candidates` 引数)、Phase 5-1の時点ではeLabFTWの生カスタムフィールド
そのものを解釈する処理は持たない (実際に渡すものが無ければ空リストのままでよい)。

`InterpretationPipeline.interpret_experiment()` 自身はレポートを返すだけで、
`ExperimentData`を書き換えることはない (`accepted`候補を実際に反映する処理は
`interpretation/apply.py::apply_interpretation_report()` に分離している。
Phase 5-3、elabftw2MaiML_phase5_design.md 13節)。
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, List, Optional, Sequence

from .conflict import Conflict, InterpretationCandidate, detect_conflicts
from .normalize import canonical_unit
from .policy import candidate_from_extracted_value
from .sections import (
    UNKNOWN_SECTION_TYPE,
    SectionClassifier,
    SectionDetector,
    TextSection,
)
from .text import TextRuleInterpreter

# ExperimentData/Step は elabftw2maiml.model にあるが、pipeline.py 自体は
# duck-typing (body_text/steps/elab_id/body属性を持つオブジェクト) で受け取る。
# elabftw2maiml.model への直接依存は避け、テストでダミーオブジェクトを渡しやすくする。

EXPERIMENT_CONTEXT = "experiment"  # 実験全体を表すcontext (elabftw2MaiML_phase5_design.md 9節)


def step_context(step_elab_id) -> str:
    """Step単位のcontextキーを生成する (elabftw2MaiML_phase5_design.md 9節)。"""
    return f"step:{step_elab_id}"


@dataclass
class InterpretationReport:
    """`InterpretationPipeline.interpret_experiment()` の結果。

    elabftw2MaiML_phase5_design.md 8節に基づく。判断材料を失わないことを重視し、
    確定候補だけでなく、全候補・競合・未分類も保持する。

    candidates:
        収集した全ての解釈候補 (構造化 + 自由記述)。
    accepted:
        MaiMLへ自動反映可能と判定された候補 (競合が無く、role/semantic_type/
        context/target が確定し、confidenceが閾値以上のもの)。
    conflicts:
        値が一致しない候補群 (`detect_conflicts()` の結果そのもの)。
    unclassified:
        値は抽出できたが、role・targetなどが未確定で自動反映できない候補
        (競合には含まれないもの)。
    sections:
        実験本文を分割した分類済みセクション (`section_detector`/`section_classifier`
        を指定した場合のみ。未指定なら空)。未知の見出しのレポート表示に使う。

        elabftw2MaiML_phase5_design.md の型定義では `list[ExtractedValue]` と
        されているが、本実装では `InterpretationCandidate` のまま保持する
        (context・confidence・unit等の判断材料を落とさずに残すため。
        `ExtractedValue` はこれらの一部 (context等) を持たない)。
    """

    candidates: List[InterpretationCandidate] = field(default_factory=list)
    accepted: List[InterpretationCandidate] = field(default_factory=list)
    conflicts: List[Conflict] = field(default_factory=list)
    unclassified: List[InterpretationCandidate] = field(default_factory=list)
    sections: List[TextSection] = field(default_factory=list)


def _is_auto_acceptable(candidate: InterpretationCandidate, confidence_threshold: float) -> bool:
    """elabftw2MaiML_phase5_design.md 10節の自動反映条件
    (競合が無いことは呼び出し側で別途確認する。ここではそれ以外の条件のみ判定する)。"""
    return (
        candidate.confidence >= confidence_threshold
        and candidate.role is not None
        and candidate.semantic_type is not None
        and candidate.context is not None
        and candidate.target is not None
    )


def partition_candidates(
    candidates: Sequence[InterpretationCandidate],
    conflicts: Sequence[Conflict],
    confidence_threshold: float = 1.0,
):
    """候補を (accepted, unclassified) に仕分ける。競合に含まれる候補は
    どちらにも入れない (`conflicts` の中にのみ残る)。"""
    conflicted_ids = {id(c) for conflict in conflicts for c in conflict.candidates}

    accepted: List[InterpretationCandidate] = []
    unclassified: List[InterpretationCandidate] = []
    for candidate in candidates:
        if id(candidate) in conflicted_ids:
            continue
        if _is_auto_acceptable(candidate, confidence_threshold):
            accepted.append(candidate)
        else:
            unclassified.append(candidate)
    return accepted, unclassified


class InterpretationPipeline:
    """自由記述からの候補収集 + 構造化候補との統合 + 競合検出 + 仕分けを行う
    (elabftw2MaiML_phase5_design.md 7節 `InterpretationPipeline` に対応)。"""

    def __init__(
        self,
        text_interpreter: Optional[TextRuleInterpreter] = None,
        confidence_threshold: float = 1.0,
        extra_text_interpreters: Optional[Sequence[Any]] = None,
        field_mapping: Optional[Any] = None,
        section_detector: Optional[SectionDetector] = None,
        section_classifier: Optional[SectionClassifier] = None,
        excluded_section_types: Sequence[str] = ("note",),
    ):
        """
        extra_text_interpreters:
            `text_interpreter` に加えて実行する追加の自由記述抽出器 (`extract(text)
            -> List[ExtractedValue]` を持つオブジェクトなら何でもよい)。
            SEM/TEM等の分野固有プロファイル (`interpretation/profiles/`) を、
            汎用の`TextRuleInterpreter`とは別クラスに保ったまま組み合わせるための
            拡張ポイント (「SEM_TEM_field_mapping_example.md」7節で議論した
            「汎用InterpreterとSEM/TEM固有ルールを分離する」構成に対応)。
            省略時 (None) は既存と全く同じ挙動 (`text_interpreter`のみ)。
        field_mapping:
            `FieldMapping` (`lookup_semantic_type()` を持つオブジェクト)。指定すると、
            自由記述由来で `role`/`target` が未確定の候補に、`semantic_type` を
            キーにこの対応表の `role`/`target` を補完する (外部化設計 3節・7節)。
            Custom Fieldと自由記述が、同じ semantic_type から同じ role/target に
            到達するための仕組み。補完は、抽出器が既に設定した値を上書きせず、
            対応表の期待単位と次元が異なる候補 (例: 期待kVに対し mA) には
            行わない。省略時 (None) は補完しない (従来通り)。
        section_detector / section_classifier:
            両方を指定すると、実験本文を見出しでセクションに分割・分類し、
            セクションごとに自由記述を抽出して、候補の context を
            `section:<section_type>:<連番>` にする (`interpretation/sections.py`)。
            異なる工程の同種の値 (「20 min」と「30 min」) が同じ
            context="experiment" に集まって不要な競合になる問題を避けるための仕組み。
            どちらか一方だけの指定は不可 (ValueError)。省略時は従来通り
            実験本文全体を context="experiment" として扱う。STEP本文には適用しない
            (STEPは `step:<id>` のまま)。
        excluded_section_types:
            自動反映の対象外にする section_type (既定: "note" = 考察・メモ)。
            該当セクションの候補は抽出するが `role`/`target` を外し、
            `unclassified` に理由付きで残す (考察文中の「0.05%や0.01%」のような
            実験条件ではない数値を誤って反映しないため)。
        """
        if (section_detector is None) != (section_classifier is None):
            raise ValueError(
                "section_detector と section_classifier は両方を指定してください")
        self._section_detector = section_detector
        self._section_classifier = section_classifier
        self._excluded_section_types = frozenset(excluded_section_types)
        self._text_interpreter = text_interpreter or TextRuleInterpreter()
        self._extra_text_interpreters = list(extra_text_interpreters) if extra_text_interpreters else []
        self._field_mapping = field_mapping
        self.confidence_threshold = confidence_threshold

    def _complete_from_mapping(self, candidate: InterpretationCandidate) -> InterpretationCandidate:
        """role/targetが未確定の自由記述候補を、semantic_typeをキーに対応表から補完する。"""
        if self._field_mapping is None:
            return candidate
        if candidate.role is not None and candidate.target is not None:
            return candidate
        rule = self._field_mapping.lookup_semantic_type(candidate.semantic_type)
        if rule is None:
            return candidate
        if (
            rule.unit is not None
            and candidate.unit is not None
            and canonical_unit(rule.unit) != canonical_unit(candidate.unit)
        ):
            return candidate
        return replace(
            candidate,
            role=candidate.role if candidate.role is not None else rule.role,
            target=candidate.target if candidate.target is not None else rule.target,
        )

    def free_text_candidates(self, exp, body_text: Optional[str] = None) -> List[InterpretationCandidate]:
        """実験本文 (`exp.body_text`) と各Step本文 (`step.body`, context=f"step:{step.elab_id}")
        から自由記述候補を収集する (elabftw2MaiML_phase5_design.md 7節 1〜3ステップ)。
        `text_interpreter`と`extra_text_interpreters`の全てを実行し、結果を連結する。

        セクション分割を有効にしている場合、実験本文は見出しごとに分けて処理し、
        context は `section:<type>:<連番>` になる (無効なら従来通り "experiment")。

        body_text:
            実験本文として使うテキスト。省略時は `exp.body_text`。`exp.body_text` は
            HTML除去時に改行が失われているため、セクション分割には
            `sections.html_to_structured_text()` で作った構造付きテキストを渡す
            (`ElabftwClient.fetch_body_structured()`)。

        `exp` は `elabftw2maiml.model.ExperimentData` を想定するが、
        `body_text`/`steps` (各要素が `elab_id`/`body` を持つ) の属性があれば
        duck-typingで動作する。
        """
        return self._collect_free_text(exp, body_text)[0]

    def _extract_from(self, text: str, context: str, interpreters) -> List[InterpretationCandidate]:
        out: List[InterpretationCandidate] = []
        for interpreter in interpreters:
            for extracted in interpreter.extract(text):
                out.append(self._complete_from_mapping(
                    candidate_from_extracted_value(extracted, context=context)))
        return out

    def _collect_free_text(self, exp, body_text: Optional[str] = None):
        """`(候補, 分類済みセクション)` を返す。"""
        candidates: List[InterpretationCandidate] = []
        sections: List[TextSection] = []
        interpreters = [self._text_interpreter] + self._extra_text_interpreters
        text = body_text if body_text is not None else exp.body_text

        if text:
            if self._section_detector is None:
                candidates.extend(self._extract_from(text, EXPERIMENT_CONTEXT, interpreters))
            else:
                sections = self._section_classifier.classify_all(
                    self._section_detector.split(text))
                for section in sections:
                    found = self._extract_from(section.body, section.context, interpreters)
                    if section.section_type in self._excluded_section_types:
                        found = [
                            replace(
                                c, role=None, target=None,
                                reason=(f"section_type={section.section_type} "
                                        f"(見出し「{section.title}」) の本文のため自動反映の対象外です"),
                            )
                            for c in found
                        ]
                    candidates.extend(found)

        for step in exp.steps or []:
            body = getattr(step, "body", None)
            if not body:
                continue
            candidates.extend(
                self._extract_from(body, step_context(step.elab_id), interpreters))

        return candidates, sections

    def interpret_experiment(
        self,
        exp,
        structured_candidates: Optional[Sequence[InterpretationCandidate]] = None,
        body_text: Optional[str] = None,
    ) -> InterpretationReport:
        """実験1件分の候補を収集・統合し、`InterpretationReport` を返す
        (elabftw2MaiML_phase5_design.md 7節)。

        structured_candidates:
            構造化フィールド (Custom Field等) からあらかじめ生成された候補。
            実際のフィールド名->semantic_type/role/target対応表 (Phase 5-2で
            設定として導入予定) を使って呼び出し側が用意する想定。Phase 5-1の
            時点では省略可能 (省略時は自由記述候補のみで競合検出・仕分けを行う)。
        body_text:
            実験本文として使うテキスト (`free_text_candidates()` 参照)。
        """
        free_text, sections = self._collect_free_text(exp, body_text)
        structured = list(structured_candidates) if structured_candidates else []
        candidates = structured + free_text

        conflicts = detect_conflicts(candidates)
        accepted, unclassified = partition_candidates(
            candidates, conflicts, self.confidence_threshold
        )

        return InterpretationReport(
            candidates=candidates,
            accepted=accepted,
            conflicts=conflicts,
            unclassified=unclassified,
            sections=sections,
        )


def format_interpretation_report(report: InterpretationReport) -> str:
    """`InterpretationReport` を人間が確認できるプレーンテキストに整形する
    (elabftw2MaiML_phase5_design.md 12節: 判定根拠から原記録まで遡れるようにする)。"""
    lines = [
        f"候補: {len(report.candidates)}件 "
        f"(自動反映可: {len(report.accepted)}件 / "
        f"競合: {len(report.conflicts)}件 / "
        f"未分類: {len(report.unclassified)}件)",
    ]

    if report.accepted:
        lines.append("")
        lines.append("[自動反映可能な候補]")
        for c in report.accepted:
            unit_label = f" {c.unit}" if c.unit else ""
            lines.append(
                f"  - {c.semantic_type}={c.value}{unit_label} "
                f"(role={c.role}, target={c.target}, context={c.context}, "
                f"source={c.source}, confidence={c.confidence})"
            )

    if report.conflicts:
        from .conflict import format_conflict_report

        lines.append("")
        lines.append(format_conflict_report(report.conflicts))

    if report.unclassified:
        lines.append("")
        lines.append("[未分類の候補 (role/target未確定などのため反映されていません)]")
        for c in report.unclassified:
            unit_label = f" {c.unit}" if c.unit else ""
            detail = f"  - {c.semantic_type}={c.value}{unit_label} (source={c.source}"
            if c.context:
                detail += f", context={c.context}"
            if c.source_text:
                detail += f", source_text={c.source_text!r}"
            if c.raw_value is not None and c.raw_value != c.value:
                detail += f", raw_value={c.raw_value!r}"
            if c.reason:
                detail += f", reason={c.reason!r}"
            detail += ")"
            lines.append(detail)

    if report.sections:
        lines.append("")
        lines.append(f"[実験本文のセクション: {len([s for s in report.sections if not s.is_preamble])}件]")
        for s in report.sections:
            if s.is_preamble:
                continue
            lines.append(f"  - {s.title!r} -> {s.section_type} (context={s.context})")
        unknown = [s for s in report.sections
                   if not s.is_preamble and s.section_type == UNKNOWN_SECTION_TYPE]
        if unknown:
            lines.append("")
            lines.append("[未知のセクション (対応表に無い見出し。本文は context付きで解釈済み。"
                         "section_mappings のYAMLへ追加すると分類できます)]")
            for s in unknown:
                lines.append(f"  - {s.title}")

    return "\n".join(lines)
