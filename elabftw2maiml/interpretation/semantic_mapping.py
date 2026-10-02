"""
Semantic Mapping (version 2 対応表) — semantic_type を中心とした共通設定
(elabftw2MaiML_semantic_mapping_externalization_design.md)。

従来 (version 1, `field_mapping.FieldMapping`) は、Custom Field名 -> semantic_type
+ role + target という対応だけを YAML で持ち、自由記述の抽出ルール (正規表現) は
Python コード内に固定されていた。そのため自由記述由来の候補には `role`/`target`
が付かず、`confidence-threshold` を下げても自動反映できなかった。

version 2 では `semantic_type` を軸に、次の全てを1つの定義 (`SemanticRule`) へ
まとめる。

    fields    : その意味種別を表す Custom Field 名 / alias
    patterns  : 自由記述から値を抽出する正規表現 (named group: value / unit)
    role / target / unit / data_type / context / required

Custom Field 経由でも自由記述経由でも、同じ `semantic_type` に到達した後は同じ
`SemanticRule` から `role`/`target`/`unit` が決まる。

YAML 例:

    version: 2
    semantic_types:
      accelerating_voltage:
        role: condition
        target: conditions
        unit: kV
        data_type: number
        fields: [加速電圧, Acceleration Voltage, HV]
        unit_aliases: {kV: [KV, kv]}
        patterns:
          - '(?:加速電圧|HV)[^\\d\\n]{0,6}(?P<value>-?\\d[\\d,]*(?:\\.\\d+)?)\\s*(?P<unit>kV|KV|kv|V)(?![A-Za-z])'

読み込み時に全ルールを検証し (設計 12節)、設定ミスは `SemanticMappingError` として
即座に報告する (誤抽出の混入を防ぐため、起動時の検証は必須)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

from .field_mapping import FieldMapping, FieldRule

# 設計 5節: role / target の許可値。
ALLOWED_ROLES = (
    "material", "condition", "result", "instrument", "creator", "vendor",
)
# `apply_interpretation_report()` が反映できる target (interpretation/apply.py)。
ALLOWED_TARGETS = ("materials", "conditions", "results", "instrument")
ALLOWED_DATA_TYPES = ("number", "string", "file")

# 自由記述の抽出は数値 (value) を前提とする。
_FREE_TEXT_DATA_TYPES = ("number", None)


class SemanticMappingError(ValueError):
    """Semantic Mapping 設定の検証エラー。メッセージに semantic_type と
    原因を含める。"""


@dataclass(frozen=True)
class SemanticRule:
    """1つの semantic_type に対する定義 (設計 13節)。

    unit:
        期待する正規化後の単位。パターンに `unit` グループが無い場合は、
        抽出値の単位としてこれを固定で使う (例: 倍率の "x")。
    unit_aliases:
        生の単位表記 -> 正規化後の単位 (例: `("KV", "kV")`, `("µA", "uA")`)。
        正規化後の単位自身 (`unit` や alias の右辺) は暗黙に有効。
        表に無い表記は、大文字小文字を無視した照合を試み、それでも無ければ
        そのまま返す (未知の単位を黙って捨てない。従来の
        `normalize_sem_tem_unit()` と同じ方針)。
    context:
        Custom Field 経由の候補に付ける既定 context (v1 の `FieldRule.context`
        相当)。自由記述経由の候補の context は常に原文の位置
        (`experiment` / `step:<id>`) から決まり、この値は使わない。
    """

    semantic_type: str
    role: Optional[str] = None
    target: Optional[str] = None
    unit: Optional[str] = None
    data_type: Optional[str] = None
    context: Optional[str] = None
    required: bool = False
    fields: Tuple[str, ...] = ()
    patterns: Tuple[str, ...] = ()
    unit_aliases: Tuple[Tuple[str, str], ...] = ()

    def compiled_patterns(self) -> List["re.Pattern[str]"]:
        return [re.compile(p) for p in self.patterns]

    def normalize_unit(self, raw_unit: Optional[str]) -> Optional[str]:
        """抽出した生の単位表記を正規化する。`raw_unit` が None なら `unit`
        (固定単位) を返す。"""
        if raw_unit is None:
            return self.unit
        table: Dict[str, str] = {}
        if self.unit:
            table[self.unit] = self.unit
        for raw, norm in self.unit_aliases:
            table[raw] = norm
            table.setdefault(norm, norm)
        if raw_unit in table:
            return table[raw_unit]
        folded = {k.lower(): v for k, v in table.items()}
        return folded.get(raw_unit.lower(), raw_unit)


class SemanticMapping:
    """`SemanticRule` の集合。Custom Field 名からの引き当て (`lookup_field`) と、
    自由記述の抽出 (`ConfiguredTextRuleInterpreter`) の両方の共通ソース。"""

    def __init__(self, rules: Mapping[str, SemanticRule]):
        self._rules: Dict[str, SemanticRule] = dict(rules)
        self._by_field: Dict[str, SemanticRule] = {}
        for rule in self._rules.values():
            self._validate_rule(rule)
            for name in rule.fields:
                key = name.strip()
                other = self._by_field.get(key)
                if other is not None and other is not rule:
                    raise SemanticMappingError(
                        f"Custom Field名 {key!r} が複数の semantic_type "
                        f"({other.semantic_type!r}, {rule.semantic_type!r}) に"
                        f"定義されています"
                    )
                self._by_field[key] = rule

    # -- 構築 -----------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> "SemanticMapping":
        version = data.get("version")
        if version != 2:
            raise SemanticMappingError(
                f"SemanticMapping は version: 2 の対応表のみ読み込めます "
                f"(version={version!r})。version: 1 は FieldMapping を使います"
            )
        raw_types = data.get("semantic_types")
        if not isinstance(raw_types, dict) or not raw_types:
            raise SemanticMappingError("semantic_types が空、または辞書ではありません")

        rules: Dict[str, SemanticRule] = {}
        for name, spec in raw_types.items():
            if not isinstance(name, str) or not name.strip():
                raise SemanticMappingError(f"semantic_type 名が不正です: {name!r}")
            spec = spec or {}
            if not isinstance(spec, dict):
                raise SemanticMappingError(f"{name}: 定義が辞書ではありません")
            unknown = set(spec) - _ALLOWED_KEYS
            if unknown:
                raise SemanticMappingError(
                    f"{name}: 未知のキー {sorted(unknown)} があります "
                    f"(許可: {sorted(_ALLOWED_KEYS)})"
                )
            rules[name] = SemanticRule(
                semantic_type=name,
                role=spec.get("role"),
                target=spec.get("target"),
                unit=spec.get("unit"),
                data_type=spec.get("data_type"),
                context=spec.get("context"),
                required=bool(spec.get("required", False)),
                fields=_as_str_tuple(name, "fields", spec.get("fields")),
                patterns=_as_str_tuple(name, "patterns", spec.get("patterns")),
                unit_aliases=_flatten_unit_aliases(name, spec.get("unit_aliases")),
            )
        return cls(rules)

    @classmethod
    def from_yaml_file(cls, path: str) -> "SemanticMapping":
        data = _load_yaml(path)
        return cls.from_dict(data)

    # -- 検証 (設計 12節) -----------------------------------------------------

    @staticmethod
    def _validate_rule(rule: SemanticRule) -> None:
        name = rule.semantic_type
        if rule.role is not None and rule.role not in ALLOWED_ROLES:
            raise SemanticMappingError(
                f"{name}: role={rule.role!r} は不正です (許可: {list(ALLOWED_ROLES)})"
            )
        if rule.target is not None and rule.target not in ALLOWED_TARGETS:
            raise SemanticMappingError(
                f"{name}: target={rule.target!r} は不正です "
                f"(apply_interpretation_report() が反映できるのは "
                f"{list(ALLOWED_TARGETS)} のみ)"
            )
        if rule.data_type is not None and rule.data_type not in ALLOWED_DATA_TYPES:
            raise SemanticMappingError(
                f"{name}: data_type={rule.data_type!r} は不正です "
                f"(許可: {list(ALLOWED_DATA_TYPES)})"
            )
        if rule.patterns and rule.data_type not in _FREE_TEXT_DATA_TYPES:
            raise SemanticMappingError(
                f"{name}: patterns (自由記述の抽出) は数値のみ対応です "
                f"(data_type={rule.data_type!r})"
            )
        for pattern in rule.patterns:
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise SemanticMappingError(
                    f"{name}: 正規表現がコンパイルできません: {pattern!r} ({exc})"
                ) from exc
            groups = compiled.groupindex
            if "value" not in groups:
                raise SemanticMappingError(
                    f"{name}: 正規表現に named group (?P<value>...) が必要です: {pattern!r}"
                )
            extra = set(groups) - {"value", "unit"}
            if extra:
                raise SemanticMappingError(
                    f"{name}: 未知の named group {sorted(extra)} があります "
                    f"(使えるのは value / unit のみ): {pattern!r}"
                )
            if "unit" not in groups and rule.unit is None:
                raise SemanticMappingError(
                    f"{name}: 正規表現に (?P<unit>...) が無い場合は、固定単位として "
                    f"unit を定義する必要があります: {pattern!r}"
                )
            if compiled.search(""):
                raise SemanticMappingError(
                    f"{name}: 空文字列に一致する正規表現は使えません: {pattern!r}"
                )

    # -- 参照 -----------------------------------------------------------------

    @property
    def rules(self) -> Dict[str, SemanticRule]:
        return dict(self._rules)

    def lookup_field(self, field_name: str) -> Optional[SemanticRule]:
        """Custom Field名 (または alias) から `SemanticRule` を引く。"""
        return self._by_field.get(field_name.strip())

    def __len__(self) -> int:
        return len(self._rules)

    def __contains__(self, semantic_type: str) -> bool:
        return semantic_type in self._rules

    def pattern_rules(self) -> List[SemanticRule]:
        """自由記述の抽出パターンを持つルール (定義順)。"""
        return [r for r in self._rules.values() if r.patterns]

    def to_field_mapping(self) -> FieldMapping:
        """Custom Field 側の既存処理 (`candidate_from_field` /
        `build_structured_candidates` / `find_missing_required_fields`) へ
        そのまま渡せる `FieldMapping` を生成する。

        `fields` を持つルールだけが対象。先頭の field 名を主キー、残りを
        alias として登録する。"""
        rules: Dict[str, FieldRule] = {}
        for rule in self._rules.values():
            if not rule.fields:
                continue
            primary, *aliases = [f.strip() for f in rule.fields]
            rules[primary] = FieldRule(
                semantic_type=rule.semantic_type,
                role=rule.role,
                target=rule.target,
                unit=rule.unit,
                context=rule.context,
                data_type=rule.data_type,
                required=rule.required,
                aliases=tuple(aliases),
            )
        return FieldMapping(rules)


_ALLOWED_KEYS = {
    "role", "target", "unit", "data_type", "context", "required",
    "fields", "patterns", "unit_aliases",
}


def _as_str_tuple(name: str, key: str, value) -> Tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise SemanticMappingError(f"{name}: {key} は文字列のリストで指定してください")
    return tuple(value)


def _flatten_unit_aliases(name: str, value) -> Tuple[Tuple[str, str], ...]:
    """`{正規化後の単位: [生の表記, ...]}` を `((生の表記, 正規化後), ...)` へ。"""
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise SemanticMappingError(f"{name}: unit_aliases は辞書で指定してください")
    pairs: List[Tuple[str, str]] = []
    for normalized, raws in value.items():
        if isinstance(raws, str):
            raws = [raws]
        if not isinstance(raws, (list, tuple)) or not all(isinstance(r, str) for r in raws):
            raise SemanticMappingError(
                f"{name}: unit_aliases[{normalized!r}] は文字列のリストで指定してください"
            )
        pairs.extend((raw, str(normalized)) for raw in raws)
    return tuple(pairs)


def _load_yaml(path: str) -> dict:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - 環境依存
        raise ImportError(
            "対応表YAMLの読み込みには pyyaml が必要です。`pip install pyyaml` を実行してください。"
        ) from exc
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise SemanticMappingError(f"{path}: YAMLのトップレベルが辞書ではありません")
    return data


@dataclass(frozen=True)
class LoadedMapping:
    """`load_mapping_file()` の戻り値。version に依らず CLI が同じ形で扱える。

    field_mapping:
        Custom Field 側の対応表 (v1/v2 共通)。
    text_interpreters:
        `InterpretationPipeline(extra_text_interpreters=...)` に渡す
        自由記述抽出器のリスト。v1 は従来通り `SemTemTextRuleInterpreter` 1つ
        (後方互換)、v2 は YAML の patterns から作る
        `ConfiguredTextRuleInterpreter`。
    """

    version: int
    field_mapping: FieldMapping
    text_interpreters: List[object] = field(default_factory=list)


def load_mapping_file(path: str) -> LoadedMapping:
    """`version` に応じて v1 (`FieldMapping`) / v2 (`SemanticMapping`) を
    読み分ける (設計 17節: 既存 YAML を廃止せず段階移行する)。"""
    data = _load_yaml(path)
    version = data.get("version", 1)
    if version == 1:
        from .profiles import SemTemTextRuleInterpreter

        return LoadedMapping(
            version=1,
            field_mapping=FieldMapping.from_dict(data),
            text_interpreters=[SemTemTextRuleInterpreter()],
        )
    if version == 2:
        from .configured_text import ConfiguredTextRuleInterpreter

        mapping = SemanticMapping.from_dict(data)
        return LoadedMapping(
            version=2,
            field_mapping=mapping.to_field_mapping(),
            text_interpreters=[ConfiguredTextRuleInterpreter(mapping)],
        )
    raise SemanticMappingError(f"{path}: 未対応の version です: {version!r}")
