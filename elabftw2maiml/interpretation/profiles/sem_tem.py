"""
SEM/TEM観察に特有の自由記述抽出ルール (`SemTemTextRuleInterpreter`)。

汎用の `interpretation/text.py` (`TextRuleInterpreter`: 温度・時間・質量・体積・
回転数・pH) には単位を追加しない方針とし、SEM/TEM固有の意味種別
(加速電圧・作動距離・倍率・プローブ電流・試料傾斜角・粒径・格子縞間隔・
カメラ長) は、このモジュールに完全に分離して実装する
(SEM_TEM_field_mapping_example.md を素材にした、汎用テキストでの誤検出
リスクについての検討の結果)。

設計上の制約 (このモジュールを変更・拡張する際は必ず維持すること):

1. **意味キーワード + 数値 + 単位の組み合わせのみを対象とする。**
   単位だけが書かれている表現 ("5 kV", "10 nm", "100 pA" 等、直前に対応する
   意味キーワードが無いもの) は、意味種別を確定できないため**一切抽出しない**
   (値も返さない)。これはPhase 1development planの「曖昧なものは抽出しない」
   という既存方針の延長であり、`TextRuleInterpreter`が単位・数値のみで
   温度等を判定しているのと矛盾するように見えるが、既存の単位 (℃, rpm, pH等)
   は分野を問わずほぼ一意に意味が決まる一方、kV/mm/nm/pA等はSEM/TEM
   以外の文脈でも頻出し、キーワード無しでは意味を確定できないため、より
   保守的な扱いとする。
   なお「一致しても意味種別を確定できない値をunclassifiedとして残す」という
   考え方自体は、意味種別が確定した候補について
   `role`/`target`が未設定なら`interpretation/pipeline.py`の
   `partition_candidates()`が既に`unclassified`に振り分ける (このモジュールが
   `role`/`target`を設定することは無い)。意味種別そのものが確定しない
   キーワード無しの生の数値・単位は、それ以前の段階として一切候補化しない
   (unclassifiedにすら値として現れない) という、より厳格側の実装を選んでいる。

2. **倍率 (magnification) は、ローマ字 "x"/"X" 単体を単位として扱わない。**
   "50000x" や "x50000" のような表記は、SEM/TEM分野以外の変数名・型番等との
   区別が困難なため、初期実装では対象外とする。安全に判定できる以下の表現の
   みを対象とする:
     - "倍率" キーワードを伴う表現 ("倍率 50,000" / "倍率50,000倍" /
       "倍率 50,000×" / "magnification: 50000")
     - 数値 + "倍" (漢字) + 観察・撮影等の文脈語 ("50,000倍で観察した" 等)。
       "倍" (漢字) 単体では希釈倍率等 (SEM/TEM分野に限らない一般的な用法) との
       混同を避けるため、キーワードまたは観察文脈語のいずれかを必須とする。

3. `ExtractedValue.confidence` は既存の規約通り、正規表現が完全一致した事実
   そのものを表すため常に 1.0 とする (`method="regex"`のまま変更しない)。
   `method`を汎用の`TextRuleInterpreter`と同じ`"regex"`のままにしているのは、
   `interpretation/policy.py`の`candidate_from_extracted_value()`が
   `source`省略時に`f"free_text_{extracted.method}"`から既定confidenceを
   決定する仕組みに、変更無しでそのまま乗せるため
   (結果として`DEFAULT_SOURCE_CONFIDENCE["free_text_regex"]`の0.95が適用される。
   `policy.py`側の変更は不要)。SEM/TEM固有の抽出であることは`semantic_type`
   の値自体で判別できる (これらの意味種別は`TextRuleInterpreter`側からは
   決して出力されない)。

4. 対象はPhase 1として次の8種のみ (第2段階は実際の自由記述を収集してから
   別途追加する): accelerating_voltage, working_distance, magnification,
   probe_current, specimen_tilt, particle_size, lattice_spacing,
   camera_length。
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ..text import ExtractedValue

# Phase 1で対応する意味種別の一覧 (ドキュメント・テストからの参照用)。
SEM_TEM_PHASE1_SEMANTIC_TYPES = (
    "accelerating_voltage",
    "working_distance",
    "magnification",
    "probe_current",
    "specimen_tilt",
    "particle_size",
    "lattice_spacing",
    "camera_length",
)

# SEM/TEM固有の単位正規化テーブル。text.py の `_UNIT_NORMALIZATION` とは
# 完全に分離している (汎用インタープリタの単位表には一切追加しない)。
_UNIT_NORMALIZATION = {
    "accelerating_voltage": {"kV": "kV", "KV": "kV", "kv": "kV", "V": "V"},
    "working_distance": {"mm": "mm"},
    "probe_current": {
        "pA": "pA", "nA": "nA",
        "µA": "uA", "μA": "uA", "uA": "uA",
    },
    "specimen_tilt": {"deg": "deg", "°": "deg", "度": "deg"},
    "lattice_spacing": {"nm": "nm", "Å": "angstrom", "pm": "pm"},
    "particle_size": {
        "nm": "nm", "µm": "um", "μm": "um", "um": "um", "mm": "mm",
    },
    "camera_length": {"mm": "mm", "cm": "cm", "m": "m"},
    # 倍率は表記 (倍/×/x/X) に関わらず常に "x" に正規化する (抽出コード側で固定)。
}


def normalize_sem_tem_unit(semantic_type: str, raw_unit: Optional[str]) -> Optional[str]:
    """SEM/TEM固有の生の単位表記を正規化する。text.pyの`normalize_unit()`と
    同じ考え方だが、対応表は完全に別に持つ。テーブルに無い表記はそのまま返す。"""
    if raw_unit is None:
        return None
    table = _UNIT_NORMALIZATION.get(semantic_type, {})
    if raw_unit in table:
        return table[raw_unit]
    return table.get(raw_unit.lower(), raw_unit)


def _to_number(raw: str):
    cleaned = raw.replace(",", "")
    return float(cleaned) if "." in cleaned else int(cleaned)


# キーワードと数値の間に許す区切り文字 (空白・"="・":"・"："・助詞等)。
# 数値・改行はまたがない (無関係な数値に飛び越えて一致しないようにするため)。
_GAP = r"[^\d\n]{0,6}"
_NUM = r"(-?\d[\d,]*(?:\.\d+)?)"

# (semantic_type, 正規表現) : 「意味キーワード + 数値 + 単位」を1つの正規表現で
# 要求する。単位表記の候補は長い表記を先に置く (例: "mm"を"m"より先に)。
_KEYWORD_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("accelerating_voltage", re.compile(
        r"(?:加速電圧|HV)" + _GAP + _NUM + r"\s*(kV|KV|kv|V)(?![A-Za-z])")),
    ("working_distance", re.compile(
        r"(?:作動距離|WD)" + _GAP + _NUM + r"\s*(mm)(?![A-Za-z])")),
    ("probe_current", re.compile(
        r"(?:プローブ電流|ビーム電流|照射電流)" + _GAP + _NUM
        + r"\s*(pA|nA|µA|μA|uA)(?![A-Za-z])")),
    ("specimen_tilt", re.compile(
        r"(?:試料傾斜角|傾斜角|[Tt][Ii][Ll][Tt])" + _GAP + _NUM
        + r"\s*(deg|°|度)(?![A-Za-z])")),
    ("lattice_spacing", re.compile(
        r"(?:格子縞間隔|格子縞|[Dd]-spacing|dスペーシング)" + _GAP + _NUM
        + r"\s*(nm|Å|pm)(?![A-Za-z])")),
    ("particle_size", re.compile(
        r"(?:粒径|[Pp]article\s*[Ss]ize)" + _GAP + _NUM
        + r"\s*(nm|µm|μm|um|mm)(?![A-Za-z])")),
    ("camera_length", re.compile(
        r"カメラ長" + _GAP + _NUM + r"\s*(mm|cm|m)(?![A-Za-z])")),
]

# 倍率は単位が常に"x"固定で、かつ「キーワード必須」or「観察文脈語必須」という
# 別ロジックのため、他の意味種別とは別に扱う。
_MAGNIFICATION_PATTERNS: List["re.Pattern[str]"] = [
    # "倍率 50,000" / "倍率50,000倍" / "倍率 50,000×"
    re.compile(r"倍率" + _GAP + _NUM + r"\s*[倍×xX]?"),
    # "50,000倍で観察" / "50,000倍にて撮影" 等 (漢字"倍"単体は希釈等との混同を
    # 避けるため、観察・撮影等の文脈語を必須とする)
    re.compile(_NUM + r"\s*倍\s*(?:で|にて)\s*(?:観察|撮影|測定|確認)"),
    # "magnification: 50000" / "Magnification 50000"
    re.compile(r"magnification\s*[:：]?\s*" + _NUM, re.IGNORECASE),
]


def _spans_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return not (a_end <= b_start or b_end <= a_start)


def _dedup_overlapping(
    entries: List[Tuple[int, int, ExtractedValue]]
) -> List[Tuple[int, int, ExtractedValue]]:
    """同じ値を指す一致が複数の正規表現で重複して検出された場合、範囲が重なり
    (完全な包含関係でなくてもよい)、かつ semantic_type/value/unit が同一の
    一致は1つにまとめる。

    例: "倍率50,000倍で観察" は「倍率...」パターン (0〜9文字目) と
    「...倍で観察」パターン (2〜12文字目、"倍率"の分だけ後ろから始まる) の
    両方に一致するが、範囲が重なり値も同一のため後者を除く
    (どちらの範囲も他方を完全には包含しないため、単純な包含判定だけでは
    重複を除けない)。"""
    by_span_desc = sorted(entries, key=lambda e: (e[0], -(e[1] - e[0])))
    kept: List[Tuple[int, int, ExtractedValue]] = []
    for start, end, value in by_span_desc:
        is_duplicate = any(
            _spans_overlap(start, end, k_start, k_end)
            and value.semantic_type == k_value.semantic_type
            and value.value == k_value.value
            and value.unit == k_value.unit
            for k_start, k_end, k_value in kept
        )
        if is_duplicate:
            continue
        kept.append((start, end, value))
    kept.sort(key=lambda e: e[0])
    return kept


class SemTemTextRuleInterpreter:
    """SEM/TEM観察の自由記述から、意味キーワードを伴う数値・単位のみを
    ルールベースで抽出する。汎用の`TextRuleInterpreter`とは完全に独立した
    別クラスであり、両方の抽出結果が必要な場合は呼び出し側で両方を呼んで
    結果を連結すること (このモジュールは`TextRuleInterpreter`を変更しない)。
    """

    def extract(self, text: Optional[str]) -> List[ExtractedValue]:
        """`text`中に現れる、意味キーワードを伴うSEM/TEM関連の値を抽出する。
        一致が無ければ空リストを返す。戻り値は原文中に現れた順。

        意味キーワードを伴わない単位・数値のみの表現 ("5 kV"単体等) は
        意図的に抽出しない (モジュールdocstring参照)。"""
        if not text:
            return []

        raw_entries: List[Tuple[int, int, ExtractedValue]] = []

        for semantic_type, pattern in _KEYWORD_PATTERNS:
            for m in pattern.finditer(text):
                raw_unit = m.group(2)
                unit = normalize_sem_tem_unit(semantic_type, raw_unit)
                value = ExtractedValue(
                    semantic_type=semantic_type,
                    value=_to_number(m.group(1)),
                    unit=unit,
                    source_text=text,
                    method="regex",
                    confidence=1.0,
                )
                raw_entries.append((m.start(), m.end(), value))

        for pattern in _MAGNIFICATION_PATTERNS:
            for m in pattern.finditer(text):
                value = ExtractedValue(
                    semantic_type="magnification",
                    value=_to_number(m.group(1)),
                    unit="x",
                    source_text=text,
                    method="regex",
                    confidence=1.0,
                )
                raw_entries.append((m.start(), m.end(), value))

        deduped = _dedup_overlapping(raw_entries)
        return [value for _start, _end, value in deduped]
