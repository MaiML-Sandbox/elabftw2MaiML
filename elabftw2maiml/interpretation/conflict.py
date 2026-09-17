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
from decimal import Decimal
from typing import Dict, List, Optional, Tuple, Union

Number = Union[int, float, Decimal, str]


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
    raw_value:
        構造化フィールドの正規化前の原値 (Phase 5-3 fix:
        `interpretation/normalize.py`)。eLabFTWのExtra Fieldsは値と単位を分けて
        持つ仕組みが無く、"200 kV"のように単位が値の文字列に混在することがある
        ため、`value`は正規化後 (数値のみ) にする一方、原記録との照合のために
        変換前の値をここに保持する。正規化を行わない値 (自由記述からの抽出、
        文字列型のフィールド等) は None のままでよい。
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
    raw_value: Optional[Number] = None


@dataclass(frozen=True)
class Conflict:
    """同じ概念 (semantic_type, context, role, target) について、情報源ごとに
    値が一致しない候補群。

    role/target:
        このグループを特定した際の`role`/`target` (コードレビュー 2026-09-17
        4.2対応)。グループ内にrole/targetが未確定 (`None`) の候補しか無い場合は
        `None`のままになる。同じ`semantic_type`・`context`でもroleが異なれば
        別グループ (別の`Conflict`) になり得るため、レポート表示で区別できる
        よう保持している。"""

    semantic_type: str
    context: Optional[str]
    candidates: Tuple[InterpretationCandidate, ...] = field(default_factory=tuple)
    role: Optional[str] = None
    target: Optional[str] = None


GroupKey = Tuple[str, Optional[str], Optional[str], Optional[str]]


def _bucket_by_wildcard_field(
    candidates: List[InterpretationCandidate], get_field
) -> Dict[Optional[str], List[InterpretationCandidate]]:
    """`role`/`target`のような属性について、次のルールでバケットへ分割する
    (コードレビュー 2026-09-17 4.2対応):

    - その属性が確定している (Noneでない) 候補は、同じ値を持つ候補同士でのみ
      同じバケットに入る (異なる値同士は別バケット = 比較対象外)。
    - その属性が未確定 (None) の候補は「ワイルドカード」として、確定値を持つ
      全てのバケットに重複して含まれる (自由記述等、role/targetがまだ
      決まっていない候補が、構造化候補と正しく突き合わせられるようにするため)。
    - 誰も確定値を持たない場合は、全体を1つのバケット (key=None) として返す。
    """
    explicit_values = {get_field(c) for c in candidates if get_field(c) is not None}
    if not explicit_values:
        return {None: list(candidates)}
    buckets: Dict[Optional[str], List[InterpretationCandidate]] = {}
    for value in explicit_values:
        buckets[value] = [c for c in candidates if get_field(c) == value or get_field(c) is None]
    return buckets


def group_candidates(candidates: List[InterpretationCandidate]) -> Dict[GroupKey, List[InterpretationCandidate]]:
    """候補を (semantic_type, context) でグルーピングしたのち、`role`・`target`
    についても以下のルールで細分化する (コードレビュー 2026-09-17 4.2対応:
    「競合判定がroleとtargetを考慮していない」への対応)。

    - 両候補の`role`が確定していて異なる場合は、別グループとして扱う
      (例: `role="material"`の質量と`role="result"`の質量は、意味が異なる別概念
      のため、値が食い違っていても競合として扱わない)。
    - 一方の`role`が未確定 (`None`) の場合は、確定側の`role`を持つグループとも
      比較対象になる (自由記述からの抽出直後などroleが未確定の候補が、
      構造化候補と正しく突き合わせられるようにするため)。
    - `target`についても同様のルールを適用する。

    戻り値のキーは `(semantic_type, context, role, target)` の4要素。
    `role`/`target`が`None`の候補は複数のグループに重複して含まれ得る
    (ワイルドカード的な扱いのため)。"""
    by_semantic_context: Dict[Tuple[str, Optional[str]], List[InterpretationCandidate]] = defaultdict(list)
    for c in candidates:
        by_semantic_context[(c.semantic_type, c.context)].append(c)

    groups: Dict[GroupKey, List[InterpretationCandidate]] = {}
    for (semantic_type, context), group in by_semantic_context.items():
        role_buckets = _bucket_by_wildcard_field(group, lambda c: c.role)
        for role_key, role_group in role_buckets.items():
            target_buckets = _bucket_by_wildcard_field(role_group, lambda c: c.target)
            for target_key, target_group in target_buckets.items():
                groups[(semantic_type, context, role_key, target_key)] = target_group
    return groups


def _values_agree(candidates: List[InterpretationCandidate]) -> bool:
    """グループ内の全候補が同じ (value, unit) を指しているかどうか。"""
    distinct = {(c.value, c.unit) for c in candidates}
    return len(distinct) <= 1


def detect_conflicts(candidates: List[InterpretationCandidate]) -> List[Conflict]:
    """候補群を (semantic_type, context, role, target) でグルーピングし、
    グループ内の値が一致しない場合のみ `Conflict` として返す
    (`group_candidates()`のワイルドカードルールにより、role/targetが未確定の
    候補は複数グループに重複して属し得る。コードレビュー 2026-09-17 4.2対応)。

    候補が1件だけのグループや、複数件あっても全て同じ (value, unit) のグループは
    「関連付けのみ」で競合ではないため、戻り値には含まれない
    (development plan 9節: 「一方で他方を上書きしない」「両方を候補として保持する」の
    うち、実際に食い違いがある場合だけを競合として抽出する)。

    戻り値は各グループの最初の候補が入力candidatesに出現する順を保つ
    (role/targetが確定した候補が複数グループに重複することは無いため、
    そちら基準では出現順が一意に定まる)。
    """
    grouped = group_candidates(candidates)
    candidate_order = {id(c): i for i, c in enumerate(candidates)}

    def first_occurrence(group: List[InterpretationCandidate]) -> int:
        return min(candidate_order[id(c)] for c in group)

    conflicts: List[Conflict] = []
    for key in sorted(grouped.keys(), key=lambda k: first_occurrence(grouped[k])):
        group = grouped[key]
        if len(group) >= 2 and not _values_agree(group):
            semantic_type, context, role, target = key
            conflicts.append(
                Conflict(
                    semantic_type=semantic_type, context=context,
                    candidates=tuple(group), role=role, target=target,
                )
            )

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
        labels = []
        if conflict.context:
            labels.append(f"context={conflict.context}")
        if conflict.role:
            labels.append(f"role={conflict.role}")
        label = f" ({', '.join(labels)})" if labels else ""
        lines.append(f"[競合] {conflict.semantic_type}{label}:")
        for c in conflict.candidates:
            unit_label = f" {c.unit}" if c.unit else ""
            detail = f"    - {c.value}{unit_label}  (source={c.source}, confidence={c.confidence}"
            if c.source_text:
                detail += f", source_text={c.source_text!r}"
            if c.raw_value is not None and c.raw_value != c.value:
                detail += f", raw_value={c.raw_value!r}"
            if c.reason:
                detail += f", reason={c.reason!r}"
            detail += ")"
            lines.append(detail)
    return "\n".join(lines)
