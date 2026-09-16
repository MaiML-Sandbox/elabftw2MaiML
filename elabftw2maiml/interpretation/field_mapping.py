"""
Phase 5-2: 設定可能なフィールドマッピング (elabftw2MaiML_phase5_design.md 5節・13節、
SEM_TEM_field_mapping_example.md 2節・6節)。

eLabFTWのCustom Field名を、単純に `フィールド名 -> semantic_type` だけに対応付ける
のではなく、`role` (material/condition/result/instrument/vendor/creator等)・
`semantic_type`・単位・`context` (実験全体か、どのStep/工程か)・MaiMLへの出力先
(`target`) までを1組の設定として持つ (SEM_TEM_field_mapping_example.md 2節)。

対応表はPythonコードへ固定せず、YAML/dictとして外部化する
(elabftw2MaiML_phase5_design.md 5節)。研究分野・組織・テンプレートが変われば
対応表だけを差し替えられるようにするため。

このモジュールは実際のeLabFTW APIやExperimentDataには依存しない。呼び出し側
(将来のPhase 5-3、または利用者のスクリプト) が「フィールド名・値・単位」の組を
`RawField` として渡すことで、`InterpretationCandidate` を得る。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple, Union

from .conflict import InterpretationCandidate
from .normalize import parse_numeric_with_unit

Number = Union[int, float, str]


@dataclass(frozen=True)
class RawField:
    """eLabFTWのCustom Field 1件分の生の値 (呼び出し側が用意する)。

    name:
        eLabFTW上のCustom Field名 (`FieldMapping` の主キーまたはaliasに一致させる)。
    value:
        値そのもの (数値・文字列など。単位変換は行わないため、事前に数値化しておく
        ことを推奨する)。
    unit:
        eLabFTW側で入力された生の単位表記 (任意)。省略時は `FieldRule.unit`
        (対応表で定義された期待単位) をそのまま採用する。
    group:
        eLabFTW上のCustom Field Group名 (任意)。`context_override`/`role_override`
        を呼び出し側がGroupから決める際の手がかりに使う想定
        (SEM_TEM_field_mapping_example.md 7節: 同名フィールドをGroupで区別する)。
    """

    name: str
    value: Number
    unit: Optional[str] = None
    group: Optional[str] = None


@dataclass(frozen=True)
class FieldRule:
    """1つのCustom Field名に対する対応付けルール
    (SEM_TEM_field_mapping_example.md 2節の対応表1行分)。"""

    semantic_type: str
    role: Optional[str] = None
    target: Optional[str] = None
    unit: Optional[str] = None
    context: Optional[str] = None
    data_type: Optional[str] = None
    required: bool = False
    aliases: Tuple[str, ...] = ()


class FieldMapping:
    """フィールド名 (またはalias) -> `FieldRule` の対応表。

    YAML例 (SEM_TEM_field_mapping_example.md 6節相当):

        version: 1
        fields:
          加速電圧:
            aliases: [Acceleration Voltage, HV]
            role: condition
            semantic_type: accelerating_voltage
            unit: kV
            context: sem_acquisition
            target: condition_properties
            required: true
    """

    def __init__(self, rules: Dict[str, FieldRule]):
        # 表引きは正規化した名前 (前後空白除去) で行う。大文字小文字は区別する
        # (日本語フィールド名が主で、英字aliasも大文字小文字が意味を持ちうるため)。
        self._by_name: Dict[str, FieldRule] = {}
        for name, rule in rules.items():
            self._register(name, rule)

    def _register(self, name: str, rule: FieldRule) -> None:
        key = name.strip()
        self._by_name[key] = rule
        for alias in rule.aliases:
            self._by_name[alias.strip()] = rule

    @classmethod
    def from_dict(cls, data: dict) -> "FieldMapping":
        """`{"fields": {field_name: {...}}}` 形式の辞書から構築する
        (YAML/JSONをそのまま読み込んだ結果を渡す想定)。"""
        fields = data.get("fields", {}) or {}
        rules: Dict[str, FieldRule] = {}
        for name, spec in fields.items():
            spec = spec or {}
            aliases = tuple(spec.get("aliases", []) or [])
            rules[name] = FieldRule(
                semantic_type=spec["semantic_type"],
                role=spec.get("role"),
                target=spec.get("target"),
                unit=spec.get("unit"),
                context=spec.get("context"),
                data_type=spec.get("data_type"),
                required=bool(spec.get("required", False)),
                aliases=aliases,
            )
        return cls(rules)

    @classmethod
    def from_yaml_file(cls, path: str) -> "FieldMapping":
        """YAMLファイルから構築する。`pip install pyyaml` が必要
        (対応表の外部化そのものはこのモジュールの必須機能ではないため、
        yamlパッケージは遅延importにしてある)。"""
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - 環境依存
            raise ImportError(
                "FieldMapping.from_yaml_file() には pyyaml が必要です。"
                " `pip install pyyaml` を実行してください。"
            ) from exc

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls.from_dict(data or {})

    def lookup(self, field_name: str) -> Optional[FieldRule]:
        """フィールド名 (またはalias) から `FieldRule` を引く。未定義なら None。"""
        return self._by_name.get(field_name.strip())

    def __contains__(self, field_name: str) -> bool:
        return self.lookup(field_name) is not None

    def __len__(self) -> int:
        return len(self._by_name)


def candidate_from_field(
    raw: RawField,
    field_mapping: FieldMapping,
    *,
    context_override: Optional[str] = None,
    role_override: Optional[str] = None,
    source: str = "custom_field",
    confidence: float = 1.0,
) -> Optional[InterpretationCandidate]:
    """`RawField` を対応表で解決し、`InterpretationCandidate` を1件返す。
    対応表に無いフィールド名の場合は None を返す (呼び出し側は
    `build_structured_candidates()` を使うと、未定義フィールドをまとめて
    回収できる)。

    context_override / role_override:
        対応表の `context`/`role` より優先する。SEM_TEM_field_mapping_example.md
        7節の「同名フィールドをGroupやStepで区別する」ケース向け
        (例:「加速電圧」フィールドが SEM/TEM 共通でも、`raw.group` や
        呼び出し側が把握しているStep種別から `sem_acquisition`/`tem_acquisition`
        を指定できる)。

    単位を持つフィールド (対応表またはraw側で`unit`が指定されているもの) は、
    競合判定の前に `interpretation.normalize.parse_numeric_with_unit()` で
    正規化する (Phase 5-3 fix)。eLabFTWのExtra Fieldsは値と単位を分けて持つ
    仕組みが無く、`"200 kV"`のように単位が値の文字列に混在することがあるため、
    正規化せずに渡すと自由記述側 (数値+別属性の単位) と型・表現が食い違い、
    実際には一致している値が誤って競合と判定されてしまう。

    正規化できなかった場合 (数値として解釈できない、または対応表の期待単位と
    次元が異なる。例: `"not measured"`や期待`kV`に対する`"200 mA"`) は、
    値をそのまま (`raw_value`として) 保持しつつ、`role`/`target`を`None`に
    強制する (自動反映させず、`interpretation.pipeline`の仕分けで
    `unclassified`に回すため。黙って捨てず、原値と理由を`reason`に残す)。
    """
    rule = field_mapping.lookup(raw.name)
    if rule is None:
        return None

    expected_unit = raw.unit if raw.unit is not None else rule.unit
    role = role_override if role_override is not None else rule.role
    target = rule.target
    reason = None
    value = raw.value
    unit = expected_unit
    raw_value = None

    if expected_unit is not None:
        normalized = parse_numeric_with_unit(raw.value, expected_unit=expected_unit)
        if normalized is not None:
            value = normalized.value
            unit = normalized.unit
            raw_value = normalized.raw_value
        else:
            role = None
            target = None
            raw_value = raw.value
            reason = (
                f"値を正規化できないため自動反映を無効化しました "
                f"(raw_value={raw.value!r}, expected_unit={expected_unit!r})"
            )

    return InterpretationCandidate(
        semantic_type=rule.semantic_type,
        value=value,
        source=source,
        confidence=confidence,
        unit=unit,
        context=context_override if context_override is not None else rule.context,
        role=role,
        target=target,
        reason=reason,
        raw_value=raw_value,
    )


def build_structured_candidates(
    fields: Sequence[RawField],
    field_mapping: FieldMapping,
    *,
    context_for: Optional[callable] = None,
    source: str = "custom_field",
    confidence: float = 1.0,
) -> Tuple[List[InterpretationCandidate], List[RawField]]:
    """複数の `RawField` を一括で解決する。

    戻り値は `(candidates, unmapped_fields)`。対応表に定義の無いフィールドは
    黙って捨てず `unmapped_fields` として返す
    (elabftw2MaiML_phase5_design.md 15節-9: 「未知のフィールド名が失われずレポートに
    残る」)。

    context_for:
        `RawField -> Optional[str]` の関数。指定すると、そのフィールドの
        `context_override` として使う (例: `lambda f: f.group and GROUP_CONTEXTS.get(f.group)`)。
        省略時は対応表の `context` をそのまま使う。
    """
    candidates: List[InterpretationCandidate] = []
    unmapped: List[RawField] = []

    for raw in fields:
        context_override = context_for(raw) if context_for else None
        candidate = candidate_from_field(
            raw, field_mapping,
            context_override=context_override,
            source=source,
            confidence=confidence,
        )
        if candidate is None:
            unmapped.append(raw)
        else:
            candidates.append(candidate)

    return candidates, unmapped
