"""
自由記述 (実験本文・Stepの記述など) から、科学実験で頻出する値
(温度・時間・質量・体積・回転数・pH) をルールベース (正規表現+単位辞書) で
抽出する TextRuleInterpreter。

elabftw2MaiML_phase1_development_plan.md 8節・14節「第一弾では行わないこと」に
基づく初期実装であり、LLMには依存せず、単位・数値など曖昧さの少ない表現の抽出に
限定する (自然言語の意味解釈全般への対応は次段階=LLMInterpreterに委ねる)。

StructuredRuleInterpreter (structured.py) と同様、抽出結果は単なる値ではなく
判定根拠 (原文・抽出方法・確信度) を伴う `ExtractedValue` として保持する。

このモジュール単体では eLabFTW の ExperimentData / MaiML 出力とはまだ接続しない。
接続 (競合検出・ExperimentDataへの統合) は development plan のPhase 4/5で扱う。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Union

Number = Union[int, float]


@dataclass(frozen=True)
class ExtractedValue:
    """自由記述から抽出された1つの値。

    semantic_type:
        抽出した値の意味種別。"temperature" / "duration" / "mass" / "volume" /
        "rotation_speed" / "ph" のいずれか。
    value:
        抽出した数値 (小数点を含まない表記はint、含む表記はfloatとして保持する)。
    unit:
        正規化後の単位 (例: "degC" / "min" / "mg" / "mL" / "rpm")。
        pH のように単位を持たない値は None。
    source_text:
        抽出元として渡された原文 (呼び出し時の文字列全体。抽出根拠として
        後から原文に遡れるようにするため、一致した部分文字列ではなく全文を保持する)。
    method:
        抽出方法。現状は常に "regex" (将来 LLMInterpreter を追加した場合は
        "llm" 等になる想定)。
    confidence:
        抽出の確信度。正規表現による明確な一致では 1.0 とする。
    """

    semantic_type: str
    value: Number
    unit: Optional[str]
    source_text: str
    method: str = "regex"
    confidence: float = 1.0


# 単位表記の正規化テーブル (原文中の生の単位表記 -> 正規化後の単位)。
# TextRuleInterpreter.extract() の内部で使うが、単体テストしやすいよう
# normalize_unit() として分離しておく
# (development plan 8節「抽出後には、単位表記を正規化する処理も分離しておく」)。
_UNIT_NORMALIZATION = {
    "temperature": {
        "℃": "degC", "°C": "degC", "°c": "degC",
        "K": "K", "k": "K",
    },
    "duration": {
        "s": "s", "sec": "s", "secs": "s", "秒": "s",
        "min": "min", "mins": "min", "分": "min",
        "h": "h", "hr": "h", "hrs": "h", "時間": "h", "時": "h",
    },
    "mass": {
        "mg": "mg", "g": "g", "kg": "kg",
    },
    "volume": {
        "µL": "uL", "μL": "uL",
        "mL": "mL",
        "L": "L",
    },
    "rotation_speed": {
        "rpm": "rpm", "RPM": "rpm",
    },
}


def normalize_unit(semantic_type: str, raw_unit: Optional[str]) -> Optional[str]:
    """生の単位表記 (例: "℃", "分", "RPM") を正規化後の単位 (例: "degC", "min",
    "rpm") に変換する。テーブルに無い表記の場合はそのまま返す (未知の単位を
    黙って捨てないため)。"""
    if raw_unit is None:
        return None
    table = _UNIT_NORMALIZATION.get(semantic_type, {})
    if raw_unit in table:
        return table[raw_unit]
    return table.get(raw_unit.lower(), raw_unit)


def _to_number(text: str) -> Number:
    return float(text) if "." in text else int(text)


_NUM = r"(-?\d+(?:\.\d+)?)"

# (semantic_type, 正規表現) のリスト。数値は常にグループ1、単位はグループ2
# (pHのみ単位を持たないためグループ1のみ)。同じ接頭辞を持つ単位表記
# ("min"/"mins"、"sec"/"secs" 等) は、短い方が先に一致して長い方を食べ損なう
# ことのないよう、長い表記を先に置く。
_PATTERNS = [
    # -- 温度 (℃, °C, K) -----------------------------------------------------
    ("temperature", re.compile(_NUM + r"\s*(℃|°C|°c)")),
    ("temperature", re.compile(r"(?<![A-Za-z0-9])" + _NUM + r"\s*(K)(?![A-Za-z])")),
    # -- 時間 (s, sec, min, h および 分/秒/時間/時) -----------------------------
    ("duration", re.compile(
        _NUM + r"\s*(secs|mins|hrs|sec|min|hr|時間|分|秒|時|s|h)(?![A-Za-z])")),
    # -- 質量 (mg, g, kg) ------------------------------------------------------
    ("mass", re.compile(_NUM + r"\s*(mg|kg|g)(?![A-Za-z])")),
    # -- 体積 (µL, μL, mL, L) ---------------------------------------------------
    ("volume", re.compile(_NUM + r"\s*(µL|μL|mL|L)(?![A-Za-z])")),
    # -- 回転数 (rpm) -----------------------------------------------------------
    ("rotation_speed", re.compile(_NUM + r"\s*(rpm|RPM)(?![A-Za-z])")),
    # -- pH (pH 7, pH=7.0, pH7) ----------------------------------------------------
    ("ph", re.compile(r"[pP][hH]\s*[=:]?\s*" + _NUM)),
]


class TextRuleInterpreter:
    """自由記述 (実験本文・Stepの記述など) から温度・時間・質量・体積・回転数・
    pHをルールベースで抽出する。

    第一弾のスコープ (development plan 14節) では、LLMによる意味解釈は行わず、
    単位・数値・既知のキーワードなど明確に判定できる範囲に限定する。
    """

    def extract(self, text: Optional[str]) -> List[ExtractedValue]:
        """`text` 中に現れる全ての該当値を抽出する。一致が無ければ空リストを返す。
        戻り値は原文中に現れた順に並ぶ。"""
        if not text:
            return []

        matches = []
        for semantic_type, pattern in _PATTERNS:
            for m in pattern.finditer(text):
                if semantic_type == "ph":
                    value_str = m.group(1)
                    canonical_unit = None
                else:
                    value_str, raw_unit = m.group(1), m.group(2)
                    canonical_unit = normalize_unit(semantic_type, raw_unit)
                matches.append((
                    m.start(),
                    ExtractedValue(
                        semantic_type=semantic_type,
                        value=_to_number(value_str),
                        unit=canonical_unit,
                        source_text=text,
                        method="regex",
                        confidence=1.0,
                    ),
                ))

        # 原文中に現れた位置順に安定ソートする (走査するパターンの順序ではなく、
        # 実際に文中に出てきた順に揃えることで、結果が読みやすくテストの期待値も
        # 書きやすくなる)。
        matches.sort(key=lambda pair: pair[0])
        return [value for _position, value in matches]
