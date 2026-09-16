"""
ExperimentData (eLabFTWから取得したデータ) -> MaiML <maiml> ルート要素、への変換本体。

マッピング方針 (READMEにも記載):

  document/creator   = 実験のカスタムフィールド (既定候補: 「使用装置」等) から取得。
                        フィールドが無ければこのコンバータ自身にフォールバックする。
  document/vendor    = 同様にカスタムフィールド (既定候補: 「装置メーカー」等) から取得。
                        creator/vendorとも見つからなければ Deltablot にフォールバックする。
  document/owner     = eLabFTW実験のオーナー (ユーザー)。ユーザーIDから名前ベースUUIDを生成、
                        同一ユーザーは常に同一UUIDになる。
  document/instrument = カスタムフィールドからcreatorを特定できた場合のみ、同じ表示名で追加する。
  document/date       = 実験の date (作成日)。

  protocol/method     = 実験1件 = 1 method。
  protocol/.../pnml    = 材料place -> 各StepのTransition(直列) -> 結果place という
                          単純な直列ペトリネット。
  protocol/.../program/instruction
                       = eLabFTWの各Step (Steps API) = 1 instruction。
  materialTemplate     = リンクされたeLabFTW Item (試料・機器等) は最初のSTEPのみが消費する。
                          実データ (試料の性質など) はここに持たせる。
  conditionTemplate    = 実験のExtra Fields (カスタムフィールド) をまとめて1つ。最初のSTEPが消費する。
  resultTemplate        = STEPごとに1つ作成する (MaiMLの一般的な考え方: 各STEPがmaterial/condition/resultを
                          持つ、というモデルに準拠)。
                            - 最初のSTEP(R1): templateRefは持たない。M1(materialTemplate)からの入力は
                              pnml上のarc (p_material_in -> transition -> p_result_out) と、双方が
                              同じplaceRefを共有することで表現済みであり、templateRefで
                              materialTemplateを指すとMaiML標準の「templateRefは同種の要素同士のみ
                              参照可」という制約に反するため (resultTemplate.templateRefは
                              resultTemplateのみを参照できる)。
                            - 途中のSTEP: templateRefでR1を参照 (前STEPの結果を入力材料として引き継ぐ、
                              というMaiMLの一般的な考え方の簡易実装)。汎用データコンテナは持たない
                              (参照先の情報を自動継承するため)
                            - 最後のSTEP: templateRefで直前のSTEPのresultTemplateを参照。ここに実験の
                              実データ (本文/タグ/添付ファイル) を持たせる

  data/results/material/condition/result
                       = 上記テンプレートに対応する実測値インスタンス。resultは同じ接続パターンを
                         instanceRef (templateRefのインスタンス層版) で反映する (最初のSTEPは
                         instanceRefも持たない。理由はresultTemplateと同様)。

  eventLog             = 各StepについてSTART/COMPLETEイベントを記録。
                          結果を記録した最終Stepのイベントに resultsRef を付与し、
                          R-16 (lifecycle:transition=complete 必須) を満たす。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from lxml import etree

from . import maiml_xml as mx
from .model import ExperimentData, Party, PropertyValue, LinkedItem, Step
from .uuids import new_uuid, named_uuid

VENDOR_KEY = ("elabftw-vendor", "deltablot")
CREATOR_SOFTWARE_VERSION = "0.2.0"  # このコンバータ自体のバージョン (プロジェクトのリリース番号と一致させる)。
                                     # 上げたらMaiML内の「変換ソフトウェア」エンティティのUUIDが変わる。


def _dt(value: Optional[datetime]) -> str:
    """xs:dateTime (ISO8601) 文字列に変換。tz naiveならUTC付与。"""
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _property_from_pv(pv: PropertyValue) -> etree._Element:
    return mx.property_el(
        key=pv.key,
        xsi_type=pv.xsi_type,
        value=pv.value,
        values=pv.values,
        description=pv.description,
        format_string=pv.format_string,
        units=pv.units,
        scale_factor=pv.scale_factor,
    )


class MaimlBuilder:
    def __init__(self, ns_prefix: str = "ns1", ns_uri: str = "https://example.org/maiml/mylab",
                 elab_host: str = "elabftw.local"):
        self.ns_prefix = ns_prefix
        self.ns_uri = ns_uri
        self.elab_host = elab_host

    # -- creator/vendor/owner のParty定義 -------------------------------

    def _software_creator(self) -> Party:
        return Party(
            key=f"elabftw-to-maiml/{CREATOR_SOFTWARE_VERSION}",
            name=f"{self.ns_prefix}:elabftw-to-maiml",
            description="eLabFTW REST API v2 (elabapi-python) 経由で取得した実験データをMaiMLに変換するツール",
        )

    def _vendor(self) -> Party:
        return Party(key="|".join(VENDOR_KEY), name=f"{self.ns_prefix}:Deltablot",
                     description="eLabFTW開発元")

    # -- document ---------------------------------------------------------

    def _build_document(self, exp: ExperimentData) -> etree._Element:
        creator_party = exp.creator or self._software_creator()
        vendor_party = exp.vendor or self._vendor()

        creator_uuid = named_uuid(creator_party.key)
        vendor_uuid = named_uuid(vendor_party.key)

        vendor_el = mx.E(
            "vendor",
            *mx.global_content(vendor_uuid, description=vendor_party.name),
            id="vendor_of_creator",
        )

        instrument_els = []
        instrument_ref_els = []
        for idx, instrument_party in enumerate(exp.instruments):
            instrument_uuid = named_uuid(instrument_party.key)
            instrument_id = f"instrument_{idx + 1}"
            instrument_els.append(mx.E(
                "instrument",
                *mx.global_content(instrument_uuid, description=instrument_party.name),
                id=instrument_id,
            ))
            instrument_ref_els.append(mx.ref_el("instrumentRef", instrument_id, f"iref_creator_{idx + 1}"))

        creator_children = [mx.ref_el("vendorRef", "vendor_of_creator", "vref_creator")]
        creator_children.extend(instrument_ref_els)

        creator_el = mx.E(
            "creator",
            *mx.global_content(creator_uuid, description=creator_party.name),
            *creator_children,
            id="creator_instrument",
        )

        owner_party = exp.owner or Party(key=f"elabftw-unknown-owner@{self.elab_host}", name="unknown")
        owner_uuid = named_uuid(owner_party.key)
        owner_el = mx.E(
            "owner",
            *mx.global_content(owner_uuid, description=owner_party.name),
            id="owner_experiment",
        )

        doc_uuid = new_uuid()
        doc_children = mx.global_content(doc_uuid, description=exp.title)
        document_children = [creator_el, vendor_el, owner_el, *instrument_els]
        return mx.E(
            "document",
            *doc_children,
            *document_children,
            mx.text_el("date", _dt(exp.date)),
            id=f"document_exp{exp.elab_id}",
        )

    # -- protocol -----------------------------------------------------------

    def _build_protocol(self, exp: ExperimentData):
        """戻り値: (protocol要素, method_id, program_id, {step_elab_id: instruction_id}, materialTemplate ids, conditionTemplate id, resultTemplate id)"""
        method_id = f"method_exp{exp.elab_id}"
        pnml_id = f"pnml_exp{exp.elab_id}"
        program_id = f"prog_exp{exp.elab_id}"

        p_material_in = "p_material_in"
        p_condition_in = "p_condition_in"
        p_result_out = "p_result_out"

        steps = exp.steps or [Step(elab_id=0, title="measurement")]
        n_steps = len(steps)

        # PLACE -> STEP(transition) -> PLACE -> STEP(transition) -> ... -> PLACE
        # (MaiMLのペトリネットはtransition同士を直接つなげず、必ず間にplaceを挟む)
        places = [
            mx.E("place", id=p_material_in),
            mx.E("place", id=p_condition_in),
        ]
        transitions = []
        arcs = []
        instructions = []
        step_instruction_ids: dict = {}
        step_output_place: dict = {}  # step_key -> このSTEPの出力先place id (resultTemplateのplaceRefに使う)

        arc_n = 0
        prev_output_place = None  # 直前のSTEPの出力place (次のSTEPの入力にもなる)
        for i, step in enumerate(steps):
            step_key = step.elab_id if step.elab_id else i
            t_id = f"t_step_{step_key}"
            transitions.append(mx.E("transition", id=t_id))

            if i == 0:
                arc_n += 1
                arcs.append(mx.E("arc", id=f"a{arc_n}", source=p_material_in, target=t_id))
                arc_n += 1
                arcs.append(mx.E("arc", id=f"a{arc_n}", source=p_condition_in, target=t_id))
            else:
                arc_n += 1
                arcs.append(mx.E("arc", id=f"a{arc_n}", source=prev_output_place, target=t_id))

            is_last = (i == n_steps - 1)
            if is_last:
                out_place = p_result_out
            else:
                out_place = f"p_mid_{step_key}"
                places.append(mx.E("place", id=out_place))
            arc_n += 1
            arcs.append(mx.E("arc", id=f"a{arc_n}", source=t_id, target=out_place))

            step_output_place[step_key] = out_place
            prev_output_place = out_place

            instr_id = f"instr_step_{step_key}"
            step_instruction_ids[step_key] = instr_id
            instr_uuid = new_uuid()
            instructions.append(mx.E(
                "instruction",
                *mx.global_content(instr_uuid, description=step.title),
                mx.ref_el("transitionRef", t_id, f"tref_{t_id}"),
                id=instr_id,
            ))

        places.append(mx.E("place", id=p_result_out))

        pnml_el = mx.E(
            "pnml",
            *mx.global_content(new_uuid()),
            *places,
            *transitions,
            *arcs,
            id=pnml_id,
        )

        # materialTemplate: リンクされたItem 1件につき1つ (無ければ汎用1つ)
        # -> 最初のSTEPのみが消費する。2つ目以降のSTEPは独自のmaterialTemplateを
        #    作らず、前STEPのresultTemplateを実質的な入力材料として扱う。
        material_templates = []
        material_template_ids = []
        materials = exp.materials or [LinkedItem(elab_id=0, title="(no linked item)")]
        for item in materials:
            tmpl_id = f"mattmpl_{item.elab_id}"
            material_template_ids.append(tmpl_id)
            props = [_property_from_pv(p) for p in item.properties]
            material_templates.append(mx.E(
                "materialTemplate",
                *mx.global_content(new_uuid(), description=item.title, properties=props),
                mx.ref_el("placeRef", p_material_in, f"pref_mat_{item.elab_id}"),
                id=tmpl_id,
            ))
        # (2つ目以降のSTEPはmaterialTemplateを作らず、最初のSTEPのresultTemplate(R1)への
        #  templateRefで入力材料を引き継ぐ。M1自体への参照は、最初のSTEPのresultTemplateでは
        #  templateRefとして持たず、pnmlのarcと共有placeRefで表現する)

        cond_tmpl_id = f"condtmpl_exp{exp.elab_id}"
        cond_props = [_property_from_pv(p) for p in exp.condition_properties]
        condition_template = mx.E(
            "conditionTemplate",
            *mx.global_content(new_uuid(), properties=cond_props),
            mx.ref_el("placeRef", p_condition_in, "pref_cond"),
            id=cond_tmpl_id,
        )

        # resultTemplate: STEPごとに1つ作る。
        #   - 最初のSTEP: templateRefを持たない。M1(materialTemplate)からの入力はpnmlのarc
        #     (p_material_in -> transition -> p_result_out) と、双方が同じ場所(place)を
        #     参照していることで既に表現されている。templateRefでmaterialTemplateを直接
        #     参照すると、「resultTemplate.templateRefはresultTemplateのみを参照できる」
        #     というMaiML標準の制約(共通指示書4.2/5.1、REF-02)に反するため、
        #     ここでは意図的に templateRef を省略する (templateRef はminOccurs=0=任意要素)。
        #   - 途中のSTEP: templateRefで最初のSTEPのresultTemplate(R1)を参照。汎用データコンテナなし
        #     (M1をそのまま参照として引き継ぐ、という位置づけをR1経由で表現する)
        #   - 最後のSTEP: templateRefで直前のSTEPのresultTemplateを参照。
        #     ここに実験の実データ (本文/タグ/添付ファイル) を持たせる
        result_templates = []
        step_result_template_ids = []  # [(step_key, tmpl_id), ...] STEP順
        prev_result_tmpl_id = None
        first_result_template_id = None
        n_steps = len(steps)
        for i, step in enumerate(steps):
            step_key = step.elab_id if step.elab_id else i
            tmpl_id = f"restmpl_step_{step_key}"

            is_first = (i == 0)
            is_last = (i == n_steps - 1)

            if is_first:
                # 最初のSTEP -> templateRefなし (pnmlのarcで既に表現されているため)
                template_ref_el = None
            elif not is_last:
                # 途中のSTEP -> 最初のSTEPのresultTemplate(R1)へ
                template_ref_el = mx.ref_el("templateRef", first_result_template_id, f"tref_{tmpl_id}")
            else:
                # 最後のSTEP -> 直前のSTEPのresultTemplateへ
                template_ref_el = mx.ref_el("templateRef", prev_result_tmpl_id, f"tref_{tmpl_id}")

            props = list(exp.result_properties) if is_last else []
            # 実験全体の実データ (本文/タグ) は最後のSTEPのみに持たせる。
            # 最初/途中のSTEPは汎用データコンテナを持たない (参照先から自動継承)

            content_children = mx.global_content(new_uuid(), properties=[_property_from_pv(p) for p in props])

            children = list(content_children)
            children.append(mx.ref_el("placeRef", step_output_place[step_key], f"pref_res_{step_key}"))
            if template_ref_el is not None:
                children.append(template_ref_el)

            result_templates.append(mx.E("resultTemplate", *children, id=tmpl_id))
            step_result_template_ids.append((step_key, tmpl_id))
            if is_first:
                first_result_template_id = tmpl_id
            prev_result_tmpl_id = tmpl_id

        program_el = mx.E(
            "program",
            *mx.global_content(new_uuid()),
            *instructions,
            *material_templates,
            condition_template,
            *result_templates,
            id=program_id,
        )

        method_el = mx.E(
            "method",
            *mx.global_content(new_uuid(), description=exp.title),
            pnml_el,
            program_el,
            id=method_id,
        )

        protocol_el = mx.E(
            "protocol",
            *mx.global_content(new_uuid()),
            method_el,
            id=f"protocol_exp{exp.elab_id}",
        )

        return (protocol_el, method_id, program_id, step_instruction_ids,
                material_template_ids, cond_tmpl_id, step_result_template_ids)

    # -- data ---------------------------------------------------------------

    def _build_data(self, exp: ExperimentData, material_template_ids, cond_tmpl_id, step_result_template_ids):
        results_id = f"results_exp{exp.elab_id}"

        materials = exp.materials or [LinkedItem(elab_id=0, title="(no linked item)")]
        material_instances = []
        material_instance_ids = []
        for item, tmpl_id in zip(materials, material_template_ids):
            props = [_property_from_pv(p) for p in item.properties]
            inst_id = f"material_{item.elab_id}"
            material_instance_ids.append(inst_id)
            material_instances.append(mx.E(
                "material",
                *mx.global_content(new_uuid(), description=item.title, properties=props),
                id=inst_id,
                ref=tmpl_id,
            ))
        cond_props = [_property_from_pv(p) for p in exp.condition_properties]
        condition_instance = mx.E(
            "condition",
            *mx.global_content(new_uuid(), properties=cond_props),
            id=f"condition_exp{exp.elab_id}",
            ref=cond_tmpl_id,
        )

        # result: STEPごとに1つ、対応するresultTemplateと同じ接続パターンをinstanceRefで反映する。
        #   - 最初のSTEP: instanceRefを持たない (resultTemplate側と同じ理由。result.instanceRefは
        #     同種のresultのみ参照可であり、materialインスタンスは参照できないため。
        #     M1からの入力はpnmlのarcと共有placeRefで既に表現されている)
        #   - 途中のSTEP: instanceRefで最初のSTEPのresultインスタンス(R1)を参照。汎用データコンテナなし
        #   - 最後のSTEP: instanceRefで直前のSTEPのresultインスタンスを参照。実データを保持
        result_instances = []
        prev_result_instance_id = None
        first_result_instance_id = None
        n_steps = len(step_result_template_ids)
        for i, (step_key, tmpl_id) in enumerate(step_result_template_ids):
            inst_id = f"result_step_{step_key}"
            is_first = (i == 0)
            is_last = (i == n_steps - 1)

            if is_first:
                instance_ref_el = None
            elif not is_last:
                instance_ref_el = mx.ref_el("instanceRef", first_result_instance_id, f"iref_{inst_id}")
            else:
                instance_ref_el = mx.ref_el("instanceRef", prev_result_instance_id, f"iref_{inst_id}")

            if is_last:
                insertions = [
                    mx.insertion_el(uri=u.uri, file_hash_b64=u.hash_b64, hash_method=u.hash_method, fmt=None)
                    for u in exp.uploads
                ]
                result_props = [_property_from_pv(p) for p in exp.result_properties]
                content_children = mx.global_content(new_uuid(), insertions=insertions, properties=result_props)
            else:
                content_children = mx.global_content(new_uuid())

            children = list(content_children)
            if instance_ref_el is not None:
                children.append(instance_ref_el)

            result_instances.append(mx.E("result", *children, id=inst_id, ref=tmpl_id))
            if is_first:
                first_result_instance_id = inst_id
            prev_result_instance_id = inst_id

        results_el = mx.E(
            "results",
            *mx.global_content(new_uuid()),
            *material_instances,
            condition_instance,
            *result_instances,
            id=results_id,
        )

        data_el = mx.E(
            "data",
            *mx.global_content(new_uuid()),
            results_el,
            id=f"data_exp{exp.elab_id}",
        )
        return data_el, results_id

    # -- eventLog -------------------------------------------------------------

    def _build_event_log(self, exp: ExperimentData, method_id: str, program_id: str,
                          step_instruction_ids: dict, results_id: str):
        steps = exp.steps or [Step(elab_id=0, title="measurement", finished_at=exp.date, is_finished=True)]

        events = []
        last_instr_id = None
        for i, step in enumerate(steps):
            key = step.elab_id if step.elab_id else i
            instr_id = step_instruction_ids[key]
            last_instr_id = instr_id
            instance_uuid = new_uuid()

            start_ts = _dt(step.started_at or exp.date)
            events.append(mx.E(
                "event",
                *mx.global_content(new_uuid(), properties=[
                    mx.property_el("concept:instance", "uuidType", value=instance_uuid),
                    mx.property_el("lifecycle:transition", "stringType", value="start"),
                    mx.property_el("time:timestamp", "dateTimeType", value=start_ts),
                ]),
                id=f"event_start_{key}",
                ref=instr_id,
            ))

            is_last = (i == len(steps) - 1)
            complete_ts = _dt(step.finished_at or exp.date)
            complete_children = mx.global_content(new_uuid(), properties=[
                mx.property_el("concept:instance", "uuidType", value=instance_uuid),
                mx.property_el("lifecycle:transition", "stringType", value="complete"),
                mx.property_el("time:timestamp", "dateTimeType", value=complete_ts),
            ])
            complete_event = mx.E("event", *complete_children, id=f"event_complete_{key}", ref=instr_id)
            if is_last:
                # R-16: 結果を記録した場合、対応するinstructionのcompleteイベントに
                # resultsRef を付与する。
                complete_event.append(mx.ref_el("resultsRef", results_id, f"resref_{key}"))
            events.append(complete_event)

        trace_id = f"trace_exp{exp.elab_id}"
        trace_el = mx.E(
            "trace",
            *mx.global_content(new_uuid()),
            *events,
            id=trace_id,
            ref=program_id,
        )

        log_id = f"log_exp{exp.elab_id}"
        log_el = mx.E(
            "log",
            *mx.global_content(new_uuid()),
            trace_el,
            id=log_id,
            ref=method_id,
        )

        event_log_el = mx.E(
            "eventLog",
            *mx.global_content(new_uuid()),
            log_el,
            id=f"eventLog_exp{exp.elab_id}",
        )
        return event_log_el

    # -- root -------------------------------------------------------------

    def build(self, exp: ExperimentData) -> etree._Element:
        nsmap = {
            None: mx.NS_MAIML,
            "xsi": mx.NS_XSI,
            "ds": mx.NS_DS,
            self.ns_prefix: self.ns_uri,
            "concept": "http://www.xes-standard.org/concept.xesext#",
            "lifecycle": "http://www.xes-standard.org/lifecycle.xesext#",
            "time": "http://www.xes-standard.org/time.xesext#",
        }
        root = etree.Element(f"{{{mx.NS_MAIML}}}maiml", nsmap=nsmap)
        root.set("version", "1.0")
        root.set("features", "nested-attributes")
        root.set(f"{{{mx.NS_XSI}}}type", "maimlRootType")

        document_el = self._build_document(exp)
        (protocol_el, method_id, program_id, step_instruction_ids,
         material_template_ids, cond_tmpl_id, step_result_template_ids) = self._build_protocol(exp)
        data_el, results_id = self._build_data(exp, material_template_ids, cond_tmpl_id, step_result_template_ids)
        event_log_el = self._build_event_log(exp, method_id, program_id, step_instruction_ids, results_id)

        root.append(document_el)
        root.append(protocol_el)
        root.append(data_el)
        root.append(event_log_el)
        return root

    def to_bytes(self, exp: ExperimentData) -> bytes:
        root = self.build(exp)
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
