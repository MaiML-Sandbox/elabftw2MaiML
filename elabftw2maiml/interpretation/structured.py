"""
構造情報 (eLabFTWのCategory/Tag/Custom Field Group) から material/condition/
result/creator/vendor/instrument の役割 (role) を判定するルールベースの
Interpreter。

elabftw_client.py に元々含まれていた `_classify_role()` /
`_classify_field_group_role()` およびそれらが参照する既定候補辞書を
移設したもの (elabftw2MaiML_phase1_development_plan.md 7節)。

このリファクタリングでは判定結果自体は変更しない
(既存の変換結果と一致している)。
唯一の違いは、判定結果が単なる文字列ではなく、判定根拠
(`source` / `reason` / `confidence`) を伴う `InterpretationResult` として
返される点である。
"""
from __future__ import annotations

import re
from typing import Optional

from .model import InterpretationResult

# リンクされたアイテム (items_links) を、カテゴリ名・タグの文字列から
# material/condition/result/creator/instrument/vendor のいずれの役割として扱うか判定する際の
# 既定候補文字列 (大文字小文字を区別せず部分一致)。
# 優先順位は _ROLE_PRIORITY の順 (先に一致した役割が採用される)。一致しなければ "material"。
DEFAULT_ROLE_CATEGORY_CANDIDATES = {
    "creator": ["Creator", "作成者", "使用装置", "使用機器"],
    "vendor": ["Vendor", "メーカー", "製造元", "Manufacturer"],
    "condition": ["Conditions", "Condition", "条件"],
    "result": ["Results", "Result", "結果"],
    "instrument": ["Resources", "Resource", "Equipment", "装置", "機器", "Instrument"],
    "material": ["Consumables", "Samples", "Sample", "試料", "材料", "Material"],
}
DEFAULT_ROLE_TAG_CANDIDATES = {k: list(v) for k, v in DEFAULT_ROLE_CATEGORY_CANDIDATES.items()}
# 一致判定を試みる順序 (material以外を先に判定し、どれにも当てはまらなければmaterial扱いにする)
_ROLE_PRIORITY = ["creator", "vendor", "condition", "result", "instrument", "material"]

# eLabFTWの「カスタムフィールドのグループ化」機能 (CUSTOM FIELDS > MATERIAL/CONDITION/... の
# ように折りたたみグループを作れる機能) で使われるグループ名から、
# material/condition/result のどの役割として扱うかを判定する際の既定候補
# (大文字小文字を区別せず部分一致)。これはリンクされたアイテムのカテゴリ/タグ判定とは別の仕組みで、
# 実験"自身"のカスタムフィールドに対して適用される。どれにも一致しないグループ (グループ無し含む)
# のフィールドは既定で "condition" として扱う (従来の挙動と互換)。
DEFAULT_FIELD_GROUP_CANDIDATES = {
    "material": ["MATERIAL", "材料", "試料", "Sample"],
    "result": ["RESULT", "RESULTS", "結果"],
    # "condition" は明示候補を指定しなくても、どれにも一致しない場合のフォールバック先になる
}
_FIELD_GROUP_ROLE_PRIORITY = ["material", "result"]  # 先に一致した方が採用され、どちらにも該当しなければ"condition"

# フォールバック (どの候補にも一致しなかった) 場合の確信度。
# ルールが明示的に一致した場合は 1.0 を用いる。
_FALLBACK_CONFIDENCE = 0.5
_MATCH_CONFIDENCE = 1.0


class StructuredRuleInterpreter:
    """eLabFTWの構造情報 (Category/Tag/Custom Field Group) による役割判定。

    現時点では既存の `elabftw_client.py` からロジックを移設しただけで、
    判定アルゴリズム自体は変更していない。既定候補はコンストラクタ引数で
    上書きできる (省略時は本モジュールの `DEFAULT_*` を使う)。
    """

    def __init__(self,
                 role_category_candidates: Optional[dict] = None,
                 role_tag_candidates: Optional[dict] = None,
                 field_group_candidates: Optional[dict] = None):
        self.role_category_candidates = dict(DEFAULT_ROLE_CATEGORY_CANDIDATES)
        if role_category_candidates:
            self.role_category_candidates.update(role_category_candidates)
        self.role_tag_candidates = dict(DEFAULT_ROLE_TAG_CANDIDATES)
        if role_tag_candidates:
            self.role_tag_candidates.update(role_tag_candidates)
        self.field_group_candidates = dict(DEFAULT_FIELD_GROUP_CANDIDATES)
        if field_group_candidates:
            self.field_group_candidates.update(field_group_candidates)

    def classify_linked_item_role(self, category_title: Optional[str], tags: Optional[str],
                                   category_candidates: Optional[dict] = None,
                                   tag_candidates: Optional[dict] = None) -> InterpretationResult:
        """
        リンクされたアイテムのカテゴリ名・タグ文字列から、material/condition/result/
        creator/instrument/vendor のどの役割として扱うかを判定する。
        大文字小文字を区別せず部分一致で判定し、_ROLE_PRIORITY の順に調べる。
        どれにも該当しなければ "material" とみなす (従来の既定動作と互換)。

        `category_candidates` / `tag_candidates` を渡した場合はコンストラクタで
        設定した既定候補の代わりにそちらを使う (呼び出し単位での上書き)。
        """
        # コンストラクタで設定した既定候補 (self.xxx) をベースに、
        # 呼び出し単位で渡された override をマージする (置き換えではなくマージ)。
        merged_category_candidates = dict(self.role_category_candidates)
        if category_candidates:
            merged_category_candidates.update(category_candidates)
        category_candidates = merged_category_candidates

        merged_tag_candidates = dict(self.role_tag_candidates)
        if tag_candidates:
            merged_tag_candidates.update(tag_candidates)
        tag_candidates = merged_tag_candidates

        # (source_label, haystack_text) のリスト。元の実装は category/tag の
        # 候補辞書を区別せず、両方をすべてのhaystack (category文字列 + 各タグ) に対して
        # 照合していたため、その挙動をそのまま再現する
        # (haystackの由来 = category/tagを`source`として報告する)。
        haystacks = []
        if category_title:
            haystacks.append(("category", category_title.lower()))
        if tags:
            haystacks.extend(
                ("tag", t.strip().lower()) for t in re.split(r"[,|]", tags) if t.strip()
            )

        for role in _ROLE_PRIORITY:
            if role == "material":
                continue  # materialは最後にフォールバックとして扱う
            cat_cands = category_candidates.get(role, [])
            tag_cands = tag_candidates.get(role, [])
            all_cands = list(cat_cands) + list(tag_cands)

            for source_label, h in haystacks:
                matched = next((c for c in all_cands if c.lower() in h), None)
                if matched:
                    return InterpretationResult(
                        role=role,
                        confidence=_MATCH_CONFIDENCE,
                        source=source_label,
                        reason=f"{source_label} {h!r} matched candidate {matched!r} for role {role!r}",
                    )

        return InterpretationResult(
            role="material",
            confidence=_FALLBACK_CONFIDENCE,
            source="default",
            reason="no category/tag candidate matched; defaulting to material",
        )

    def classify_field_group_role(self, group_name: Optional[str],
                                   field_group_candidates: Optional[dict] = None) -> InterpretationResult:
        """
        グループ名の文字列から material/result のどちらかに該当するか判定する。
        大文字小文字を区別せず部分一致。どちらにも該当しない (グループ無し含む) 場合は
        "condition" とみなす (従来の既定動作と互換)。
        """
        # コンストラクタで設定した既定候補 (self.field_group_candidates) をベースに、
        # 呼び出し単位で渡された override をマージする (置き換えではなくマージ)。
        candidates = dict(self.field_group_candidates)
        if field_group_candidates:
            candidates.update(field_group_candidates)

        if group_name:
            low = group_name.lower()
            for role in _FIELD_GROUP_ROLE_PRIORITY:
                for c in candidates.get(role, []):
                    if c.lower() in low:
                        return InterpretationResult(
                            role=role,
                            confidence=_MATCH_CONFIDENCE,
                            source="field_group",
                            reason=f"field group {group_name!r} matched candidate {c!r} for role {role!r}",
                    )

        return InterpretationResult(
            role="condition",
            confidence=_FALLBACK_CONFIDENCE,
            source="default",
            reason="no field group candidate matched (or no group); defaulting to condition",
        )
