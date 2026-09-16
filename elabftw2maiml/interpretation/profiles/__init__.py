"""SEM/TEM等、特定の観察・測定分野に固有の自由記述抽出ルールを集めるパッケージ。

汎用の `interpretation/text.py` (`TextRuleInterpreter`) には分野固有の単位を
追加しない方針とし、分野ごとの拡張はこの `profiles/` 配下に分離して追加する
(理由の詳細は `profiles/sem_tem.py` のモジュールdocstringを参照)。
"""
from .sem_tem import SemTemTextRuleInterpreter, SEM_TEM_PHASE1_SEMANTIC_TYPES

__all__ = [
    "SemTemTextRuleInterpreter",
    "SEM_TEM_PHASE1_SEMANTIC_TYPES",
]
