"""重複一致の除去 (`SemTemTextRuleInterpreter` / `ConfiguredTextRuleInterpreter` 共用)。"""
from __future__ import annotations

from typing import List, Tuple

from .text import ExtractedValue


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
