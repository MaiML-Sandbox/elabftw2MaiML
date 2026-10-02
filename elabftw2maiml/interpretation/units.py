"""
単位の次元 (dimension) と標準単位への換算 (`normalize_quantity`)。

「これは duration (時間) である」という意味判定と、「保存する単位を min に統一する」
という単位正規化を分けて扱うためのモジュール
(elabftw2MaiML_section_semantic_review_fixes.md 5節)。

`SemanticRule` に `dimension` (例: time) と `canonical_unit` (例: min) を定義すると、
自由記述から `11 h` が抽出されたとき、`semantic_type=duration` のまま `660 min` に
換算して反映できる。換算できない場合 (未知の単位・次元が異なる単位) は `None` を返し、
呼び出し側は semantic_type を保持したまま自動反映だけを見送る。

換算は `Decimal` で行い、整数になる場合は int、そうでなければ float で返す
(例: 11 h -> 660 min、0.5 h -> 30 min、300 s -> 5 min)。単位表記は
`interpretation/normalize.py::canonical_unit()` と同じ正規化後の表記 (degC, uL, um 等)
を使う。表に無い単位表記は換算できないものとして扱う (黙って別の単位とみなさない)。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, Optional, Tuple, Union

from .normalize import canonical_unit as _canonical_unit

Number = Union[int, float]

# dimension -> {単位: (係数, オフセット)}。基準単位での値 = 値 * 係数 + オフセット
# (基準単位はそれぞれ s / V / m / L / g / A / degC / rpm)。
_D = Decimal
UNIT_TABLE: Dict[str, Dict[str, Tuple[Decimal, Decimal]]] = {
    "time": {
        "s": (_D(1), _D(0)), "min": (_D(60), _D(0)), "h": (_D(3600), _D(0)),
        "d": (_D(86400), _D(0)),
    },
    "voltage": {
        "V": (_D(1), _D(0)), "kV": (_D(1000), _D(0)), "mV": (_D("0.001"), _D(0)),
    },
    "length": {
        "m": (_D(1), _D(0)), "cm": (_D("0.01"), _D(0)), "mm": (_D("0.001"), _D(0)),
        "um": (_D("0.000001"), _D(0)), "nm": (_D("0.000000001"), _D(0)),
        "pm": (_D("0.000000000001"), _D(0)), "angstrom": (_D("0.0000000001"), _D(0)),
    },
    "volume": {
        "L": (_D(1), _D(0)), "mL": (_D("0.001"), _D(0)), "uL": (_D("0.000001"), _D(0)),
    },
    "mass": {
        "g": (_D(1), _D(0)), "kg": (_D(1000), _D(0)), "mg": (_D("0.001"), _D(0)),
        "ug": (_D("0.000001"), _D(0)),
    },
    "current": {
        "A": (_D(1), _D(0)), "mA": (_D("0.001"), _D(0)), "uA": (_D("0.000001"), _D(0)),
        "nA": (_D("0.000000001"), _D(0)), "pA": (_D("0.000000000001"), _D(0)),
    },
    "temperature": {
        "degC": (_D(1), _D(0)), "K": (_D(1), _D("-273.15")),
    },
    "rotation_speed": {"rpm": (_D(1), _D(0))},
}

DIMENSIONS = tuple(UNIT_TABLE)


def units_of(dimension: str) -> Tuple[str, ...]:
    return tuple(UNIT_TABLE[dimension])


def dimension_of(unit: Optional[str]) -> Optional[str]:
    """単位から次元を引く。未知の単位は None。"""
    if unit is None:
        return None
    unit = _canonical_unit(unit)
    for dimension, table in UNIT_TABLE.items():
        if unit in table:
            return dimension
    return None


def _to_number(value: Decimal) -> Number:
    if value == value.to_integral_value():
        return int(value)
    # 浮動小数点の端数 (例: 0.30000000000000004) を残さない
    return float(f"{value:.12g}")


def normalize_quantity(
    value: Number,
    unit: Optional[str],
    dimension: str,
    canonical_unit: str,
) -> Optional[Tuple[Number, str]]:
    """`value unit` を、`dimension` の標準単位 `canonical_unit` に換算して
    `(換算後の値, canonical_unit)` を返す。換算できなければ None。

    None になる場合: `unit` が None・`unit` が `dimension` の単位ではない・
    `dimension`/`canonical_unit` が未定義 (呼び出し側の設定ミスは例外にせず、
    自動反映を見送るだけにする)。
    """
    table = UNIT_TABLE.get(dimension)
    if table is None or unit is None or canonical_unit not in table:
        return None
    source = _canonical_unit(unit)
    if source not in table:
        return None
    try:
        amount = Decimal(str(value))
    except Exception:
        return None
    s_factor, s_offset = table[source]
    t_factor, t_offset = table[canonical_unit]
    base = amount * s_factor + s_offset
    converted = (base - t_offset) / t_factor
    return _to_number(converted), canonical_unit
