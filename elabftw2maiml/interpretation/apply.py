"""
`InterpretationReport` の `accepted` 候補を、実際に `ExperimentData` へ反映する。

データモデル対称化改修 (elabftw2MaiML_model_refactoring_plan.md) 以降、
materials/conditions/results はいずれも「MaterialData/ConditionData/ResultData
(オブジェクト) + properties」という共通構造で扱う。そのため、旧バージョンで
存在した「materialだけはLinkedItemという特殊なオブジェクトなので反映処理も
特殊」という非対称性は解消され、materials/conditions/resultsの3つは全く同じ
フローで処理される (`_find_or_create_container()` + properties への追加)。

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

2. **既存の値を上書きしない。** 同じオブジェクトの `properties` に、同じキー
   (semantic_typeから生成するQName) が既に存在する場合は追加をスキップし、ログに
   残す (development plan 9節「一方で他方を上書きしない」という既存方針を、
   ExperimentDataへの反映段階でも維持するため)。

3. **`InterpretationCandidate.target` で反映先を決める。** 実際にExperimentDataへ
   反映できる`target`は次の4種類のみ:

     - `"materials"`  -> `ExperimentData.materials`  (`list[MaterialData]`)
     - `"conditions"` -> `ExperimentData.conditions` (`list[ConditionData]`)
     - `"results"`    -> `ExperimentData.results`    (`list[ResultData]`)
     - `"instrument"` -> `ExperimentData.instruments` (`list[Party]`。値を表示名と
       する`Party`を追加する。名前の重複はスキップする)

   それ以外の (未対応の) target値は、反映方法が未定義のためスキップし、
   ログにその旨を残す (黙って捨てない)。

   materials/conditions/resultsの3つは、`candidate.context` から対象オブジェクトを
   特定する (`_container_key()`):

     - contextが `None` または `"experiment"` (`interpretation.pipeline.
       EXPERIMENT_CONTEXT`) の場合: key = `f"{singular}:experiment:{experiment.
       elab_id}"`。実験全体を表す既定のオブジェクトを指す。`elabftw_client.py` が
       実験自身のカスタムフィールドから生成するMaterialData/ConditionData/
       ResultDataも同じkey規約を使うため、`--field-mapping`側の候補と自然に
       マージされ、二重にオブジェクトが分裂しない (旧: materialsだけ`elab_id=0`の
       合成LinkedItemという特殊規約に依存していた)。
     - contextが `"step:<id>"` (`interpretation.pipeline.step_context()`が返す形式)
       の場合: key = `f"{singular}:step:<id>"`。指定したStepに対応する専用の
       オブジェクトを取得・生成する (無ければ新規作成し、`step_id`にもその値を
       設定する)。
     - それ以外の任意のcontext文字列 (例: SEM/TEM対応表の`"sem_acquisition"`) も
       同様にkeyへそのまま使う。

   該当するkeyのオブジェクトが対象リストに既に無ければ新規作成し、あれば再利用する
   (properties追加先はそのオブジェクトの`properties`)。

   プロパティキー自体はcontextを含めない (旧: Step単位で`__step_n`サフィックスを
   付与していたが、対称化後はStep単位で別々のオブジェクトに分かれるため、
   オブジェクトを跨いだキー衝突が起こらず、サフィックスが不要になった)。

4. この関数は`experiment`を**破壊的に変更する** (list系属性へのappend)。
   呼び出し側で変更前の状態を保持したい場合は、あらかじめ
   `copy.deepcopy(experiment)`しておくこと。
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import List, Optional

from ..model import ConditionData, ExperimentData, MaterialData, Party, PropertyValue, ResultData
from .conflict import InterpretationCandidate
from .pipeline import EXPERIMENT_CONTEXT, InterpretationReport

# 実験自身のMATERIALグループの自己フィールドを合成MaterialDataにまとめる際、
# elabftw_client.py の fetch_experiment() が使っている規約 (elab_id=0) に合わせる。
_SYNTHETIC_MATERIAL_ELAB_ID = 0
_SYNTHETIC_MATERIAL_CATEGORY = "(interpretation-derived material)"

# target値 (複数形) -> ExperimentData属性名 / コンテナのkeyに使う単数形 / dataclass。
_TARGET_ATTR = {"materials": "materials", "conditions": "conditions", "results": "results"}
_TARGET_SINGULAR = {"materials": "material", "conditions": "condition", "results": "result"}
_CONTAINER_FACTORY = {"materials": MaterialData, "conditions": ConditionData, "results": ResultData}


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
    """`InterpretationCandidate` を、対象オブジェクト内で一意な property keyに
    変換する。

    対称化改修より前は、実験全体を表す既定context以外 (Step単位の`step:<id>`等)
    の場合にcontextをkeyへ含めていた (異なるStep由来の同じsemantic_typeが、単一の
    フラットなリストに混在していたため)。対称化後はcontextごとに別々の
    MaterialData/ConditionData/ResultDataオブジェクトへ分かれるため、
    オブジェクトを跨いだキー衝突は起こらず、常にsemantic_typeのみのkeyでよい。"""
    return f"{ns_prefix}:{_sanitize_ncname(candidate.semantic_type)}"


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


def _parse_step_id(context: Optional[str]) -> Optional[int]:
    """`"step:<id>"` 形式のcontextからStepの`elab_id`を取り出す。それ以外の
    (またはNoneの) contextなら None を返す。"""
    if not context or not context.startswith("step:"):
        return None
    suffix = context[len("step:"):]
    try:
        return int(suffix)
    except ValueError:
        return None


def _container_key(target: str, context: Optional[str], experiment_elab_id) -> str:
    singular = _TARGET_SINGULAR[target]
    if context and context != EXPERIMENT_CONTEXT:
        return f"{singular}:{context}"
    return f"{singular}:experiment:{experiment_elab_id}"


def _find_or_create_container(experiment: ExperimentData, target: str, context: Optional[str]):
    """`target`/`context`から、反映先のMaterialData/ConditionData/ResultDataを
    取得する。無ければ新規作成して`experiment`の対応するリストに追加する。"""
    attr = _TARGET_ATTR[target]
    key = _container_key(target, context, experiment.elab_id)
    container_list = getattr(experiment, attr)
    for container in container_list:
        if container.key == key:
            return container

    step_id = _parse_step_id(context)
    factory = _CONTAINER_FACTORY[target]
    is_experiment_context = context is None or context == EXPERIMENT_CONTEXT
    if target == "materials":
        container = factory(
            key=key,
            title=experiment.title if is_experiment_context
                  else f"(interpretation-derived material: {context})",
            elab_id=_SYNTHETIC_MATERIAL_ELAB_ID if is_experiment_context else None,
            category=_SYNTHETIC_MATERIAL_CATEGORY,
            step_id=step_id,
        )
    else:
        container = factory(key=key, step_id=step_id)
    container_list.append(container)
    return container


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
    instrument_names = {p.name for p in experiment.instruments}

    for candidate in report.accepted:
        target = candidate.target
        unit_label = f" {candidate.unit}" if candidate.unit else ""

        if target in _TARGET_ATTR:
            prop_key = _candidate_key(candidate, ns_prefix)
            container = _find_or_create_container(experiment, target, candidate.context)
            if any(p.key == prop_key for p in container.properties):
                logs.append(
                    f"[スキップ] {prop_key} は既に{target} (key={container.key}) に"
                    f"存在するため追加しませんでした (semantic_type={candidate.semantic_type})。"
                )
                continue
            container.properties.append(_candidate_to_property(candidate, ns_prefix))
            logs.append(
                f"[反映] {target} (key={container.key}) に {prop_key} = "
                f"{candidate.value}{unit_label} を追加しました "
                f"(source={candidate.source}, confidence={candidate.confidence:.2f})。"
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
