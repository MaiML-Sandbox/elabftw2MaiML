"""
`InterpretationCandidate` を生成する際の、情報源ごとの確信度ポリシー。

development plan Phase 4 の議論 (「自由記述由来の値は confidence=0.95、
構造化フィールドは confidence=1.0 とする」) を受けての結論は次の通り:

confidence には意味が異なる2つの層がある。

* `ExtractedValue.confidence` (text.py)
    抽出ルール (正規表現) が文字列にどの程度確実に一致したかを表す。
    正規表現が完全一致した場合は常に 1.0 とし、**text.py 側はこのモジュール
    導入後も変更しない** (「40℃で30分加熱した」に温度の正規表現が完全一致した
    という事実そのものは confidence=1.0 で問題ない)。

* `InterpretationCandidate.confidence` (conflict.py)
    その値をMaiMLへの採用候補としてどの程度信頼してよいかを表す。
    自由記述から抽出した値は、正規表現としては確実に一致していても、
    それが本当に目的の値なのか (例: 加熱温度なのか装置設定温度なのか) は
    文字列一致だけでは確定できないため、情報源 (source) 単位で
    構造化フィールドより低めに評価する。

このモジュールは、`InterpretationCandidate.confidence` のデフォルト値 (1.0) に
暗黙に依存するのではなく、候補を生成する境界 (`candidate_from_extracted_value`
など) で情報源ごとのポリシーから明示的に confidence を計算するためのもの。

`DEFAULT_SOURCE_CONFIDENCE` の値 (特に 0.95) は統計的に検証されたものではなく、
現段階では「情報源ごとの暫定的な優先度」を表す値であることに注意する。
"""
from __future__ import annotations

from typing import Optional

from .conflict import InterpretationCandidate
from .text import ExtractedValue

# 情報源 (InterpretationCandidate.source) ごとの既定confidence。
# 構造化フィールド由来 (Category/Tag/Custom Field Group/Custom Field本体) は
# 明示的にユーザ/実験者が入力・選択した値であるため 1.0。
# 自由記述からの正規表現抽出は、対象の取り違え (加熱温度 vs 装置設定温度等) の
# 余地が残るため、暫定的に 1.0 未満とする。
DEFAULT_SOURCE_CONFIDENCE = {
    "custom_field": 1.0,
    "field_group": 1.0,
    "tag": 1.0,
    "category": 1.0,
    "free_text_regex": 0.95,
}


def candidate_confidence_for_source(source: str) -> float:
    """情報源名から、候補生成時に使う既定confidenceを得る。

    `DEFAULT_SOURCE_CONFIDENCE` に無い (未知の) 情報源は 1.0 とする
    (ポリシー未定義の情報源を、ポリシー側の都合で不当に低評価しないため)。
    """
    return DEFAULT_SOURCE_CONFIDENCE.get(source, 1.0)


def candidate_from_extracted_value(
    extracted: ExtractedValue,
    *,
    context: Optional[str] = None,
    source: Optional[str] = None,
    source_confidence: Optional[float] = None,
) -> InterpretationCandidate:
    """`TextRuleInterpreter.extract()` が返す `ExtractedValue` から
    `InterpretationCandidate` を生成する。

    confidence は「抽出そのものの確実性」(`extracted.confidence`。正規表現が
    一致した事実そのものは変更しない) と「自由記述という情報源としての
    確度」(`source_confidence`。既定は `DEFAULT_SOURCE_CONFIDENCE` から
    情報源名で引く) の積とする。

    source:
        省略時は `f"free_text_{extracted.method}"` とする (regexなら
        "free_text_regex")。将来 `LLMInterpreter` を追加した場合は
        `extracted.method == "llm"` から自動的に "free_text_llm" になる想定。
    source_confidence:
        省略時は `DEFAULT_SOURCE_CONFIDENCE` を `source` で引く。呼び出し元が
        個別に上書きしたい場合 (例: 特定フォーマットの実験ノートでは
        もっと確実、等) に指定する。
    """
    resolved_source = source or f"free_text_{extracted.method}"
    if source_confidence is None:
        source_confidence = candidate_confidence_for_source(resolved_source)

    return InterpretationCandidate(
        semantic_type=extracted.semantic_type,
        value=extracted.value,
        unit=extracted.unit,
        source=resolved_source,
        confidence=extracted.confidence * source_confidence,
        context=context,
        source_text=extracted.source_text,
    )
