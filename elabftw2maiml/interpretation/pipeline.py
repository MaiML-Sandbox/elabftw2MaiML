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

from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence

from .conflict import Conflict, InterpretationCandidate, detect_conflicts
from .policy import candidate_from_extracted_value
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

        elabftw2MaiML_phase5_design.md の型定義では `list[ExtractedValue]` と
        されているが、本実装では `InterpretationCandidate` のまま保持する
        (context・confidence・unit等の判断材料を落とさずに残すため。
        `ExtractedValue` はこれらの一部 (context等) を持たない)。
    """

    candidates: List[InterpretationCandidate] = field(default_factory=list)
    accepted: List[InterpretationCandidate] = field(default_factory=list)
    conflicts: List[Conflict] = field(default_factory=list)
    unclassified: List[InterpretationCandidate] = field(default_factory=list)


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
        """
        self._text_interpreter = text_interpreter or TextRuleInterpreter()
        self._extra_text_interpreters = list(extra_text_interpreters) if extra_text_interpreters else []
        self.confidence_threshold = confidence_threshold

    def free_text_candidates(self, exp) -> List[InterpretationCandidate]:
        """実験本文 (`exp.body_text`, context="experiment") と各Step本文
        (`step.body`, context=f"step:{step.elab_id}") から自由記述候補を収集する
        (elabftw2MaiML_phase5_design.md 7節 1〜3ステップ)。`text_interpreter`と
        `extra_text_interpreters`の全てを実行し、結果を連結する。

        `exp` は `elabftw2maiml.model.ExperimentData` を想定するが、
        `body_text`/`steps` (各要素が `elab_id`/`body` を持つ) の属性があれば
        duck-typingで動作する。
        """
        candidates: List[InterpretationCandidate] = []
        interpreters = [self._text_interpreter] + self._extra_text_interpreters

        if exp.body_text:
            for interpreter in interpreters:
                for extracted in interpreter.extract(exp.body_text):
                    candidates.append(
                        candidate_from_extracted_value(extracted, context=EXPERIMENT_CONTEXT)
                    )

        for step in exp.steps or []:
            body = getattr(step, "body", None)
            if not body:
                continue
            context = step_context(step.elab_id)
            for interpreter in interpreters:
                for extracted in interpreter.extract(body):
                    candidates.append(candidate_from_extracted_value(extracted, context=context))

        return candidates

    def interpret_experiment(
        self,
        exp,
        structured_candidates: Optional[Sequence[InterpretationCandidate]] = None,
    ) -> InterpretationReport:
        """実験1件分の候補を収集・統合し、`InterpretationReport` を返す
        (elabftw2MaiML_phase5_design.md 7節)。

        structured_candidates:
            構造化フィールド (Custom Field等) からあらかじめ生成された候補。
            実際のフィールド名->semantic_type/role/target対応表 (Phase 5-2で
            設定として導入予定) を使って呼び出し側が用意する想定。Phase 5-1の
            時点では省略可能 (省略時は自由記述候補のみで競合検出・仕分けを行う)。
        """
        free_text = self.free_text_candidates(exp)
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

    return "\n".join(lines)
