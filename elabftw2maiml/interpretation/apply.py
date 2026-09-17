"""
Phase 5-3: `InterpretationReport` の `accepted` 候補を、実際に `ExperimentData` へ
反映する (elabftw2MaiML_phase5_design.md のPhase 5-3に対応)。

設計上の方針:

1. **`accepted` 以外は絶対に書き込まない。** `report.conflicts`
   (値が食い違う候補群) および `report.unclassified`
   (role/target/semantic_type/contextが未確定、またはconfidenceが閾値未満の候補)
   は、このモジュールが `ExperimentData` に反映することは無い
   (`interpretation/pipeline.py` の `partition_candidates()` が既に
   競合を`accepted`から除外しているため、構造的に競合候補が紛れ込むことは無い)。
   これらは呼び出し側が `interpretation/pipeline.py` の
   `format_interpretation_report()` (または `format_conflict_report()`) を使って
   人が確認できる形式でログ・レポートに残すこと。このモジュール自身は
   「実際に反映した/重複のためスキップした」という反映ログ (`List[str]`) のみを
   返す。

2. **既存の値を上書きしない。** 同じキー (semantic_typeから生成するQName) が
   既に対象のリストに存在する場合は追加をスキップし、ログに残す
   (development plan 9節「一方で他方を上書きしない」という既存方針を、
   ExperimentDataへの反映段階でも維持するため)。

3. **`InterpretationCandidate.target` で反映先を決める。**
   - `"condition_properties"` / `"result_properties"`:
     `ExperimentData` の同名属性 (`list[PropertyValue]`) に直接追加する。
   - `"materials"`:
     `ExperimentData.materials` (`list[LinkedItem]`) は本来「試料そのもの」
     ではなく「試料的なリンクアイテム」の集合なので、単純な`PropertyValue`の
     追加先が無い。既存の `elabftw_client.py`
     (`_split_extra_fields_by_group`/`fetch_experiment`) が
     「実験自身のMATERIALグループのカスタムフィールド」を`elab_id=0`の合成
     `LinkedItem`にまとめている挙動に合わせ、`elab_id=0`の`LinkedItem`が既に
     存在すればそれに追加し、無ければ新規に作成する。
   - `"instrument"`:
     `ExperimentData.instruments` (`list[Party]`) に、値を表示名とする
     `Party`を追加する (名前の重複はスキップする)。
   - その他の (未対応の) target値は、反映方法が未定義のためスキップし、
     ログにその旨を残す (黙って捨てない)。

4. この関数は`experiment`を**破壊的に変更する** (list系属性へのappend)。
   呼び出し側で変更前の状態を保持したい場合は、あらかじめ
   `copy.deepcopy(experiment)`しておくこと。
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import List, Optional

from ..model import ExperimentData, LinkedItem, Party, PropertyValue
from .conflict import InterpretationCandidate
from .pipeline import EXPERIMENT_CONTEXT, InterpretationReport

# 実験自身のMATERIALグループの自己フィールドを合成LinkedItemにまとめる際、
# elabftw_client.py の fetch_experiment() が使っている規約 (elab_id=0) に合わせる。
# これにより、旧来のグループ名ベースの振り分けとPhase 5の対応表ベースの振り分けが
# 両方有効な場合でも、材料が2つの別々の合成アイテムに分裂しない。
_SYNTHETIC_MATERIAL_ELAB_ID = 0
_SYNTHETIC_MATERIAL_CATEGORY = "(interpretation-derived material)"

_SUPPORTED_PROPERTY_TARGETS = ("condition_properties", "result_properties")


def _sanitize_ncname(name: str) -> str:
    """semantic_type等をQNameのローカル部として使える形に変換する。

    `elabftw2maiml/elabftw_client.py` の同名関数と同じロジックだが、
    `interpretation/` パッケージから `elabftw_client.py` を import すると
    循環importになるため (`elabftw_client.py` は既に `interpretation` パッケージを
    importしている)、意図的にここへ小さく複製している。"""
    cleaned = re.sub(r"[^0-9A-Za-z_一-龠ぁ-んァ-ヶー]", "_", name.strip())
    if cleaned and cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned or "field"


def _infer_xsi_type(value) -> str:
    """候補の値のPython型から、既存の型マッピング (README「カスタムフィールドの
    型マッピング」節) と同じ考え方でxsi:typeを推定する。

    `interpretation/normalize.py` (Phase 5-3 fix) による正規化後の値は
    `Decimal` になるため、`int`/`float`と同様に数値として扱う。"""
    if isinstance(value, bool):
        return "booleanType"
    if isinstance(value, (int, float, Decimal)):
        return "doubleType"
    return "stringType"


def _candidate_key(candidate: InterpretationCandidate, ns_prefix: str) -> str:
    """`InterpretationCandidate` を `ExperimentData`/MaiML上の一意なproperty keyに
    変換する。

    コードレビュー (2026-09-17) 4.1の指摘対応: 以前はsemantic_typeのみでkeyを
    生成しており、異なるcontext (例: `step:1`と`step:2`) の同じsemantic_typeが
    同じkeyになって、2件目が「既存キー」として黙ってスキップされる問題があった
    (複数StepでStepごとに同じ意味種別の値を記録するケースでデータが欠落する)。

    実験全体を表す既定のcontext (`EXPERIMENT_CONTEXT`。多くの対応表がこれを
    使っている) では、これまで通り`semantic_type`のみのkeyを維持し、既存の
    MaiML出力・テストとの後方互換性を保つ。それ以外のcontext (Step単位の
    `step:<id>`や、SEM/TEM対応表の`sem_acquisition`等) の場合のみ、contextを
    keyへ含めて衝突を避ける。"""
    base = _sanitize_ncname(candidate.semantic_type)
    if candidate.context and candidate.context != EXPERIMENT_CONTEXT:
        return f"{ns_prefix}:{base}__{_sanitize_ncname(candidate.context)}"
    return f"{ns_prefix}:{base}"


def _candidate_to_property(candidate: InterpretationCandidate, ns_prefix: str) -> PropertyValue:
    xsi_type = _infer_xsi_type(candidate.value)
    return PropertyValue(
        key=_candidate_key(candidate, ns_prefix),
        xsi_type=xsi_type,
        value=candidate.value,
        units=candidate.unit if xsi_type == "doubleType" else None,
        description=(
            f"interpretation由来 (source={candidate.source}, "
            f"confidence={candidate.confidence:.2f}, context={candidate.context})"
        ),
    )


def _find_synthetic_material(experiment: ExperimentData) -> Optional[LinkedItem]:
    for item in experiment.materials:
        if item.elab_id == _SYNTHETIC_MATERIAL_ELAB_ID:
            return item
    return None


def apply_interpretation_report(
    experiment: ExperimentData,
    report: InterpretationReport,
    *,
    ns_prefix: str = "ns1",
) -> List[str]:
    """`report.accepted` の候補を `experiment` へ反映する。

    戻り値は反映ログ (`List[str]`)。`report.conflicts`/`report.unclassified` は
    このモジュールでは決して書き込まない (モジュールdocstring参照)。それらを
    人が確認できる形式で残したい場合は、呼び出し側で別途
    `interpretation.pipeline.format_interpretation_report(report)` 等を使うこと。
    """
    logs: List[str] = []

    existing_property_keys = {
        target: {p.key for p in getattr(experiment, target)}
        for target in _SUPPORTED_PROPERTY_TARGETS
    }
    synthetic_material = _find_synthetic_material(experiment)
    instrument_names = {p.name for p in experiment.instruments}

    for candidate in report.accepted:
        target = candidate.target
        key = _candidate_key(candidate, ns_prefix)
        unit_label = f" {candidate.unit}" if candidate.unit else ""

        if target in _SUPPORTED_PROPERTY_TARGETS:
            if key in existing_property_keys[target]:
                logs.append(
                    f"[スキップ] {key} は既に{target}に存在するため追加しませんでした "
                    f"(semantic_type={candidate.semantic_type})。"
                )
                continue
            getattr(experiment, target).append(_candidate_to_property(candidate, ns_prefix))
            existing_property_keys[target].add(key)
            logs.append(
                f"[反映] {target} に {key} = {candidate.value}{unit_label} を追加しました "
                f"(source={candidate.source}, confidence={candidate.confidence:.2f})。"
            )

        elif target == "materials":
            if synthetic_material is None:
                synthetic_material = LinkedItem(
                    elab_id=_SYNTHETIC_MATERIAL_ELAB_ID,
                    title=experiment.title,
                    category=_SYNTHETIC_MATERIAL_CATEGORY,
                    properties=[],
                )
                experiment.materials.append(synthetic_material)
            if any(p.key == key for p in synthetic_material.properties):
                logs.append(
                    f"[スキップ] {key} は既にmaterials (合成アイテム) に存在するため"
                    f"追加しませんでした。"
                )
                continue
            synthetic_material.properties.append(_candidate_to_property(candidate, ns_prefix))
            logs.append(
                f"[反映] materials (合成アイテム) に {key} = {candidate.value}{unit_label} を"
                f"追加しました (source={candidate.source}, confidence={candidate.confidence:.2f})。"
            )

        elif target == "instrument":
            name = str(candidate.value)
            if name in instrument_names:
                logs.append(f"[スキップ] instrument「{name}」は既に存在するため追加しませんでした。")
                continue
            experiment.instruments.append(
                Party(key=f"interpretation-instrument:{name}", name=name)
            )
            instrument_names.add(name)
            logs.append(f"[反映] instrument に「{name}」を追加しました。")

        else:
            logs.append(
                f"[未対応] target={target!r} への反映方法が未定義のためスキップしました "
                f"(semantic_type={candidate.semantic_type})。手動での対応、または"
                f"apply.pyの拡張を検討してください。"
            )

    return logs
