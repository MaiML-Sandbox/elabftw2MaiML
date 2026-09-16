"""
構造化情報 (Custom Field 等) と自由記述 (TextRuleInterpreter による抽出等)、
それぞれから得られた値を突き合わせ、一致するものは関連付け、値が異なるものは
「競合 (Conflict)」として保持するためのユーティリティ。

elabftw2MaiML_phase1_development_plan.md 9節・12節 (Phase 4) に基づく。

**このモジュールは現時点では `ExperimentData`/`fetch_experiment()` にはまだ
接続していない、汎用の (単体で完結した) 突き合わせロジックである。**
既存のeLabFTWカスタムフィールドをどの semantic_type (temperature/duration/...)
に対応付けるかは、実際の運用時のフィールド命名を見た上で別途決定する
(第一弾では一方的な誤変換を避けるため、フィールド名からの自動対応付けは行わない)。

使い方の想定 (呼び出し側で候補を集める):

    candidates = [
        InterpretationCandidate(semantic_type="temperature", value=50, unit="degC",
                                 source="custom_field"),
        InterpretationCandidate(semantic_type="temperature", value=40, unit="degC",
                                 source="free_text", source_text="40℃で30分加熱した。"),
    ]
    conflicts = detect_conflicts(candidates)
    print(format_conflict_report(conflicts))
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union

Number = Union[int, float]


@dataclass(frozen=True)
class InterpretationCandidate:
    """ある概念 (semantic_type) についての、1つの情報源からの値の候補。

    semantic_type:
        値の意味種別。例: "temperature" / "duration" / "mass" / "volume" /
        "rotation_speed" / "ph"。TextRuleInterpreter.extract() が返す
        `ExtractedValue.semantic_type` と揃えることを想定している。
    value:
        候補の数値。
    source:
        値の出処。例: "custom_field" / "free_text" / "linked_item" 等。
    confidence:
        この候補の確信度 (0.0〜1.0)。
    unit:
        正規化後の単位 (例: "degC")。単位を持たない値 (pH等) は None。
    context:
        同じ semantic_type でも対象が異なる場合 (例: Step1の温度とStep2の温度) に
        区別するための任意のキー。省略時 (None) は実験全体で1つの概念として扱う。
    source_text:
        自由記述由来の場合の原文。呼び出し元が追跡可能性のために保持したい場合に使う。
    reason:
        判定根拠の説明 (任意)。
    role:
        MaiML上の役割 ("material" / "condition" / "result" 等)。Phase 5設計
        (elabftw2MaiML_phase5_design.md 4節) に基づき、フィールド名 -> semantic_type
        だけでなく role も明示することで、同じ semantic_type でも役割が異なる値
        (例: 材料の質量 vs 測定条件としての質量) を区別できるようにする。
        未確定 (自由記述からの抽出直後など) の場合は None。
    target:
        MaiMLへの実際の反映先 ("materials" / "condition_properties" /
        "result_properties" 等)。role と対になる情報で、これも未確定なら None。
        role/target が両方確定し、かつ競合が無く confidence が十分高い候補だけを
        自動反映してよい、という判断は呼び出し側 (interpretation/pipeline.py) が行う。
    """

    semantic_type: str
    value: Number
    source: str
    confidence: float = 1.0
    unit: Optional[str] = None
    context: Optional[str] = None
    source_text: Optional[str] = None
    reason: Optional[str] = None
    role: Optional[str] = None
    target: Optional[str] = None


@dataclass(frozen=True)
class Conflict:
    """同じ概念 (semantic_type, context) について、情報源ごとに値が一致しない
    候補群。"""

    semantic_type: str
    context: Optional[str]
    candidates: Tuple[InterpretationCandidate, ...] = field(default_factory=tuple)


GroupKey = Tuple[str, Optional[str]]


def group_candidates(candidates: List[InterpretationCandidate]) -> Dict[GroupKey, List[InterpretationCandidate]]:
    """候補を (semantic_type, context) でグルーピングする。"""
    groups: Dict[GroupKey, List[InterpretationCandidate]] = defaultdict(list)
    for c in candidates:
        groups[(c.semantic_type, c.context)].append(c)
    return dict(groups)


def _values_agree(candidates: List[InterpretationCandidate]) -> bool:
    """グループ内の全候補が同じ (value, unit) を指しているかどうか。"""
    distinct = {(c.value, c.unit) for c in candidates}
    return len(distinct) <= 1


def detect_conflicts(candidates: List[InterpretationCandidate]) -> List[Conflict]:
    """候補群を (semantic_type, context) でグルーピングし、グループ内の値が
    一致しない場合のみ `Conflict` として返す。

    候補が1件だけのグループや、複数件あっても全て同じ (value, unit) のグループは
    「関連付けのみ」で競合ではないため、戻り値には含まれない
    (development plan 9節: 「一方で他方を上書きしない」「両方を候補として保持する」の
    うち、実際に食い違いがある場合だけを競合として抽出する)。

    戻り値は入力候補の出現順を保つ (semantic_type, contextの初出順)。
    """
    conflicts: List[Conflict] = []
    seen_keys: List[GroupKey] = []
    grouped = group_candidates(candidates)

    # 出現順を保つため、group_candidates の辞書順ではなく候補リストを再走査する
    for c in candidates:
        key = (c.semantic_type, c.context)
        if key in seen_keys:
            continue
        seen_keys.append(key)
        group = grouped[key]
        if len(group) >= 2 and not _values_agree(group):
            conflicts.append(Conflict(semantic_type=key[0], context=key[1], candidates=tuple(group)))

    return conflicts


def format_conflict_report(conflicts: List[Conflict]) -> str:
    """競合一覧を、人間が確認できるプレーンテキストのレポートに整形する
    (development plan 9節: 「人によるレビュー時に原記録まで遡れるか」)。
    競合が無い場合は「検出された競合はありません」の1行を返す。
    """
    if not conflicts:
        return "検出された競合はありません。"

    lines = []
    for conflict in conflicts:
        context_label = f" (context={conflict.context})" if conflict.context else ""
        lines.append(f"[競合] {conflict.semantic_type}{context_label}:")
        for c in conflict.candidates:
            unit_label = f" {c.unit}" if c.unit else ""
            detail = f"    - {c.value}{unit_label}  (source={c.source}, confidence={c.confidence}"
            if c.source_text:
                detail += f", source_text={c.source_text!r}"
            if c.reason:
                detail += f", reason={c.reason!r}"
            detail += ")"
            lines.append(detail)
    return "\n".join(lines)
