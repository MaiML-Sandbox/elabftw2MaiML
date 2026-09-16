"""
Phase 5-3 fix: 構造化フィールド (Custom Field) の値を、競合判定
(`conflict.detect_conflicts()`) の前に、自由記述側と同じ内部表現 (数値+正規化後の
単位) へ揃える。

背景: eLabFTWのExtra Fieldsには値と単位を別々に持つ仕組みが無く、"200 kV"の
ように単位が値の文字列に混在することがある (実際の画面例で確認済み)。これを
正規化せずに`InterpretationCandidate.value`へ文字列のままで渡すと、自由記述側
(`TextRuleInterpreter`等) が返す数値 (int/float、単位は別属性) と型・表現が
一致せず、実際には一致している値が誤って「競合」と判定されてしまう。

このモジュールは`interpretation/field_mapping.py`の`candidate_from_field()`から
呼ばれる想定で、次を行う:

1. 生の値 (`RawField.value`。数値・文字列いずれもありうる) から、数値部分と
   単位表記を分離する:
     - `"200 kV"` -> 200, "kV"
     - `"200kV"`  -> 200, "kV"
     - `"200"`    -> 200, (単位無し。対応表の期待単位を補う)
     - `200` (既に数値) -> 200, (同上)
2. 単位表記の空白除去・µ/μ/u等の表記統一 (次元換算は行わない)。
3. 分離した単位が対応表の期待単位と一致するか確認する。一致しない場合
   (例: 期待`kV`に対し`V`や`mA`) は、**単位換算をせず**「正規化できない値」
   として扱う (初期実装のスコープ: development planレビューで指摘された
   通り、次元換算は将来の拡張とする)。
4. 数値として解釈できない値 (例: `"not measured"`) も同様に「正規化できない
   値」として扱う。

正規化できた場合は `NormalizedValue(value=Decimal, unit=正規化後の単位,
raw_value=元の値)` を返す。正規化できない場合は `None` を返し、呼び出し側が
原値 (`raw_value`) を保持したまま、その候補を自動反映の対象から外す
(role/targetを外してunclassifiedへ回す) 判断に使う。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, Union

Number = Union[int, float, str]


@dataclass(frozen=True)
class NormalizedValue:
    """正規化に成功した1つの値。"""

    value: Decimal
    unit: Optional[str]
    raw_value: Number


# µ/μ/u等、同じ意味で複数の表記がある単位・記号表記の統一。次元換算 (V<->kV等)
# はここでは行わない (初期実装のスコープ外。モジュールdocstring参照)。
_UNIT_ALIASES = {
    "um": ("µm", "μm", "um"),
    "uA": ("µA", "μA", "uA"),
    "uL": ("µL", "μL", "uL"),
    "degC": ("℃", "°C", "°c"),
    "deg": ("°", "度", "deg"),
}


def canonical_unit(raw_unit: Optional[str]) -> Optional[str]:
    """単位表記の空白除去・µ/μ/uなどの統一のみを行う (次元換算はしない)。
    空文字列やNoneはNoneに正規化する。"""
    if raw_unit is None:
        return None
    cleaned = raw_unit.strip()
    if not cleaned:
        return None
    for canonical, aliases in _UNIT_ALIASES.items():
        if cleaned in aliases:
            return canonical
    return cleaned


# 先頭の数値 (符号・小数点を含む) + 残りの単位表記、という形に緩く分離する。
# 数値と単位の間の空白は許容する ("200 kV"/"200kV" いずれも対応)。
_NUM_UNIT_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*([^\s\d]*)\s*$")


def parse_numeric_with_unit(
    raw_value, expected_unit: Optional[str] = None
) -> Optional[NormalizedValue]:
    """`raw_value` (数値または文字列) から数値と単位を分離し、`expected_unit`
    (対応表の期待単位) と次元が一致する場合のみ `NormalizedValue` を返す。

    - `raw_value` が既に数値 (int/float/Decimal) の場合、単位は`expected_unit`を
      そのまま採用する (数値自体に単位が混在することはあり得ないため)。
    - `raw_value` が文字列で、数値+単位に分離できない場合 (例: `"not measured"`)
      は `None` を返す。
    - 分離できた単位が`expected_unit`と食い違う場合 (次元違い) も、単位換算は
      行わず `None` を返す (呼び出し側で「正規化できない値」として扱う)。
    - `expected_unit`が`None`の場合 (対応表がそもそも単位を期待していない
      フィールド) は、単位の食い違いチェックは行わない。
    """
    expected_unit_canonical = canonical_unit(expected_unit)

    if isinstance(raw_value, bool):
        # bool は int のサブクラスだが、チェックボックス等の意味的な値であり
        # 数値+単位としての正規化対象ではない。
        return None

    if isinstance(raw_value, (int, float, Decimal)):
        try:
            decimal_value = Decimal(str(raw_value))
        except InvalidOperation:
            return None
        return NormalizedValue(
            value=decimal_value, unit=expected_unit_canonical, raw_value=raw_value
        )

    if not isinstance(raw_value, str):
        return None

    match = _NUM_UNIT_RE.match(raw_value)
    if not match:
        return None

    number_text, unit_text = match.groups()
    try:
        decimal_value = Decimal(number_text)
    except InvalidOperation:
        return None

    parsed_unit_canonical = canonical_unit(unit_text) if unit_text else None

    if (
        parsed_unit_canonical is not None
        and expected_unit_canonical is not None
        and parsed_unit_canonical != expected_unit_canonical
    ):
        # 次元が異なる (例: 期待"kV"に対し"V"や"mA")。単位換算は初期実装の
        # スコープ外のため、正規化できないものとして扱う。
        return None

    final_unit = parsed_unit_canonical if parsed_unit_canonical is not None else expected_unit_canonical
    return NormalizedValue(value=decimal_value, unit=final_unit, raw_value=raw_value)
