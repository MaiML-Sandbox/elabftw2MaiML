"""
設定駆動の自由記述 Interpreter (`ConfiguredTextRuleInterpreter`)。

`SemanticMapping` の各 `SemanticRule.patterns` (YAMLで外部化された正規表現) を
使って自由記述から値を抽出し、`SemanticRule` の `role` / `target` / 単位を
補完した `ExtractedValue` を返す (外部化設計 14節)。

`profiles/sem_tem.py::SemTemTextRuleInterpreter` (パターンを Python に固定) を
置き換えるもので、抽出ロジック (意味キーワード + 数値 + 単位、複数パターンで
重複一致した同一値の統合、`method="regex"`・`confidence=1.0`) は同じ。
`policy.candidate_from_extracted_value()` が `free_text_regex` の既定 0.95 を
適用する点も変わらない。

意味キーワードを伴わない単位・数値のみの表現を抽出しない方針
(SemTemTextRuleInterpreter のdocstring 制約1) は、YAML側のパターンが
「意味キーワード + 数値 + 単位」で書かれていることで維持する。この
クラス自体はパターンの内容を解釈しない。
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from ._spans import _dedup_overlapping
from .semantic_mapping import SemanticMapping
from .text import ExtractedValue


def _to_number(raw: str):
    cleaned = raw.replace(",", "")
    return float(cleaned) if "." in cleaned else int(cleaned)


class ConfiguredTextRuleInterpreter:
    """`SemanticMapping` の patterns から自由記述の値を抽出する。"""

    def __init__(self, mapping: SemanticMapping):
        self._compiled: List[Tuple[object, "re.Pattern[str]"]] = [
            (rule, pattern)
            for rule in mapping.pattern_rules()
            for pattern in rule.compiled_patterns()
        ]

    def extract(self, text: Optional[str]) -> List[ExtractedValue]:
        """`text` 中の該当値を原文中の出現順に返す。一致が無ければ空リスト。"""
        if not text:
            return []

        entries: List[Tuple[int, int, ExtractedValue]] = []
        for rule, pattern in self._compiled:
            for m in pattern.finditer(text):
                raw_unit = m.groupdict().get("unit")
                entries.append((
                    m.start(),
                    m.end(),
                    ExtractedValue(
                        semantic_type=rule.semantic_type,
                        value=_to_number(m.group("value")),
                        unit=rule.normalize_unit(raw_unit),
                        source_text=text,
                        method="regex",
                        confidence=1.0,
                        role=rule.role,
                        target=rule.target,
                    ),
                ))

        return [value for _s, _e, value in _dedup_overlapping(entries)]
