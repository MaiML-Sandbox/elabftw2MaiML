"""
判定結果モデル。

elabftw2MaiML が eLabFTW の構造情報 (Category/Tag/Custom Field Group) や、
将来追加される自由記述のルール判定・LLM判定から
material/condition/result/creator/vendor/instrument などの「役割 (role)」を
判定した際、その結果を単なる文字列としてではなく、判定根拠を伴うモデルとして
保持するためのデータクラス。

この形式は、将来 TextRuleInterpreter / LLMInterpreter を追加した際の
説明可能性・検証可能性の基盤として、第一弾の時点から導入する
(elabftw2MaiML_phase1_development_plan.md 6節)。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InterpretationResult:
    """1件の役割判定結果。

    role:
        判定された役割名。例: "material" / "condition" / "result" /
        "creator" / "vendor" / "instrument"。
    confidence:
        判定の確信度 (0.0〜1.0)。ルールが明示的に一致した場合は 1.0、
        候補にどれも一致せず既定値にフォールバックした場合はそれより
        低い値 (現状 0.5) とする。
    source:
        判定の根拠となった情報源。例: "category" / "tag" / "field_group" /
        "default" (どの候補にも一致しなかったためのフォールバック)。
    reason:
        どの規則・値に一致した (あるいは一致しなかった) かを人間が読める
        形で示す説明文。
    """

    role: str
    confidence: float
    source: str
    reason: str
