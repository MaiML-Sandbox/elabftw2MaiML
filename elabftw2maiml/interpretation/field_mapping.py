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
from .normalize import canonical_unit, parse_numeric_with_unit

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

    def all_names_for(self, rule: FieldRule) -> List[str]:
        """指定した `rule` に登録されている全ての名前 (主キー名+alias) を返す
        (コードレビュー 2026-09-17 5.1対応の補助メソッド)。"""
        return [name for name, r in self._by_name.items() if r is rule]

    def required_rules(self) -> Dict[str, FieldRule]:
        """`required: true` が設定されている各 `FieldRule` を、それぞれの
        登録名のうち最初に見つかったもの (`_register()`は主キー名を先に
        登録するため、通常はYAML上の主キー名になる) をキーとして返す
        (コードレビュー 2026-09-17 5.1対応)。同じruleが複数の名前
        (主キー+alias) で登録されていても、1回だけ数える。"""
        seen_ids = set()
        result: Dict[str, FieldRule] = {}
        for name, rule in self._by_name.items():
            if not rule.required or id(rule) in seen_ids:
                continue
            seen_ids.add(id(rule))
            result[name] = rule
        return result


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

    対応表 (`rule.unit`) と `raw.unit` の両方が指定されていて、次元
    (`interpretation.normalize.canonical_unit()`で正規化した単位) が異なる
    場合は、値の文字列を見るまでもなく自動反映を拒否する (コードレビュー
    2026-09-17 4.3対応)。以前は `raw.unit` が無条件に `rule.unit` より優先
    されており、対応表の期待単位と食い違う単位が入力されていても、その
    食い違いに気づかず `raw.unit` 側をそのまま採用して自動反映してしまう
    (例: 対応表がkVを期待しているのに、誤って"200 mA"のようにmA付きで
    記録された値をkVとして扱ってしまう) 問題があったため。

    正規化できなかった場合 (数値として解釈できない、または値の文字列に
    埋め込まれた単位が期待単位と次元が異なる。例: `"not measured"`や
    期待`kV`に対する値文字列`"200 mA"`) も同様に、
    値をそのまま (`raw_value`として) 保持しつつ、`role`/`target`を`None`に
    強制する (自動反映させず、`interpretation.pipeline`の仕分けで
    `unclassified`に回すため。黙って捨てず、原値と理由を`reason`に残す)。
    """
    rule = field_mapping.lookup(raw.name)
    if rule is None:
        return None

    role = role_override if role_override is not None else rule.role
    target = rule.target
    reason = None
    value = raw.value
    raw_value = None

    # コードレビュー (2026-09-17) 4.3対応: 以前は `raw.unit` (eLabFTW側の実際の
    # 入力単位) が無条件に `rule.unit` (対応表が期待する単位) より優先されており、
    # 対応表の期待と食い違う単位が指定された場合でも、その食い違いに気づかず
    # `raw.unit` をそのまま採用して自動反映されてしまっていた
    # (例: 対応表がAcceleratingVoltageの単位を"kV"と定義しているのに、
    # 誤って"mA"付きで値200が記録されていても、"200 mA"を"200 kV"相当として
    # 静かに反映してしまう)。
    #
    # 両方が指定されていて次元が異なる場合は、値の中身を見るまでもなく
    # 自動反映を拒否し、原値と理由を残して`unclassified`に回す。
    if (
        rule.unit is not None
        and raw.unit is not None
        and canonical_unit(rule.unit) != canonical_unit(raw.unit)
    ):
        role = None
        target = None
        raw_value = raw.value
        unit = None
        value = raw.value
        reason = (
            f"raw.unit={raw.unit!r}が対応表のunit={rule.unit!r}と一致しないため、"
            f"自動反映を無効化しました (raw_value={raw.value!r})"
        )
    else:
        expected_unit = rule.unit if rule.unit is not None else raw.unit
        unit = expected_unit

        # 単位が無いフィールドでも、対応表が data_type: number を明示している場合は
        # 正規化を行う (例: DwellTime="10"、PixelSize="0.025" のように、単位不明の
        # まま数値として記録されているフィールド)。`parse_numeric_with_unit()` は
        # expected_unit=None でも単位の食い違いチェックをスキップして数値のみを
        # 解析できるため、そのまま呼び出せる。単位も data_type も無いフィールド
        # (文字列フィールドの大半) は、従来通り正規化をスキップする (レガシー挙動)。
        should_normalize = expected_unit is not None or rule.data_type == "number"

        if should_normalize:
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


def find_missing_required_fields(
    fields: Sequence[RawField], field_mapping: FieldMapping
) -> List[str]:
    """対応表 (`field_mapping`) で `required: true` に指定されているフィールド
    のうち、実際に渡された `fields` (eLabFTWから取得した生のCustom Field群) に
    1件も存在しないものの名前一覧を返す (コードレビュー 2026-09-17 5.1対応)。

    これまで `FieldRule.required` は対応表のスキーマ上定義されているだけで、
    実際に「必須フィールドが欠落している」ことを検証する処理が無く、値を
    入力し忘れても何の警告も出ないまま変換が完了してしまっていた。

    存在確認は主キー名だけでなくaliasでも行う (`FieldMapping.lookup()`と同じ
    正規化規則: 前後の空白を除去して比較する)。値が空文字列や `None` であっても
    「フィールド自体は存在する」とみなす (値の妥当性検証は別の関心事であり、
    ここでは「eLabFTW側にそのフィールドが入力欄として存在し、何らかの値が
    記録されているか」だけを見る)。

    戻り値は空リストなら「必須フィールドの欠落なし」。呼び出し側
    (`elabftw_to_maiml.py`) は、これを致命的エラーにはせず、レポートとして
    表示することを想定している (必須フィールドが無くても他のフィールドの
    変換自体は継続できるため)。
    """
    present_names = {f.name.strip() for f in fields}
    missing: List[str] = []
    for name, rule in field_mapping.required_rules().items():
        rule_names = set(field_mapping.all_names_for(rule))
        if not (rule_names & present_names):
            missing.append(name)
    return missing
