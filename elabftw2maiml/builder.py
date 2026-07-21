"""
ExperimentData (eLabFTWから取得したデータ) -> MaiML <maiml> ルート要素、への変換本体。

マッピング方針 (READMEにも記載):

  document/creator   = このコンバータ (elabftw-to-maiml)。ソフトウェアの版が変われば別UUID。
  document/vendor    = eLabFTWの開発元 (Deltablot)。名前ベースUUIDで固定。
  document/owner     = eLabFTW実験のオーナー (ユーザー)。ユーザーIDから名前ベースUUIDを生成、
                        同一ユーザーは常に同一UUIDになる。
  document/instrument = 未使用 (0以上のため省略可)。装置管理が必要な場合はexperiment.instrumentを追加实装。
  document/date       = 実験の date (作成日)。

  protocol/method     = 実験1件 = 1 method。
  protocol/.../pnml    = 材料place -> 各StepのTransition(直列) -> 結果place という
                          単純な直列ペトリネット。
  protocol/.../program/instruction
                       = eLabFTWの各Step (Steps API) = 1 instruction。
  materialTemplate     = リンクされたeLabFTW Item (試料・機器等) 1件につき1つ。
  conditionTemplate    = 実験のExtra Fields (カスタムフィールド) をまとめて1つ。
  resultTemplate       = 実験本文・添付ファイルの参照先として1つ。

  data/results/material/condition/result
                       = 上記テンプレートに対応する実測値インスタンス。

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
CREATOR_SOFTWARE_VERSION = "1.0.0"  # このコンバータ自体のバージョン。上げたらUUIDが変わる。


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
        creator_party = self._software_creator()
        vendor_party = self._vendor()

        creator_uuid = named_uuid(creator_party.key)
        vendor_uuid = named_uuid(vendor_party.key)

        vendor_el = mx.E(
            "vendor",
            *mx.global_content(vendor_uuid, name=vendor_party.name, description=vendor_party.description),
            id="vendor_deltablot",
        )

        creator_el = mx.E(
            "creator",
            *mx.global_content(creator_uuid, name=creator_party.name, description=creator_party.description),
            mx.ref_el("vendorRef", "vendor_deltablot", "vref_deltablot"),
            id="creator_elabftw2maiml",
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
        return mx.E(
            "document",
            *doc_children,
            creator_el,
            vendor_el,
            owner_el,
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

        places = [
            mx.E("place", id=p_material_in),
            mx.E("place", id=p_condition_in),
            mx.E("place", id=p_result_out),
        ]

        steps = exp.steps or [Step(elab_id=0, title="measurement")]

        transitions = []
        arcs = []
        instructions = []
        step_instruction_ids: dict = {}

        prev_transition_id = None
        arc_n = 0
        for i, step in enumerate(steps):
            t_id = f"t_step_{step.elab_id or i}"
            transitions.append(mx.E("transition", id=t_id))

            if i == 0:
                arc_n += 1
                arcs.append(mx.E("arc", id=f"a{arc_n}", source=p_material_in, target=t_id))
                arc_n += 1
                arcs.append(mx.E("arc", id=f"a{arc_n}", source=p_condition_in, target=t_id))
            else:
                arc_n += 1
                arcs.append(mx.E("arc", id=f"a{arc_n}", source=prev_transition_id, target=t_id))

            if i == len(steps) - 1:
                arc_n += 1
                arcs.append(mx.E("arc", id=f"a{arc_n}", source=t_id, target=p_result_out))

            instr_id = f"instr_step_{step.elab_id or i}"
            step_instruction_ids[step.elab_id if step.elab_id else i] = instr_id
            instr_uuid = new_uuid()
            instructions.append(mx.E(
                "instruction",
                *mx.global_content(instr_uuid, description=step.title),
                mx.ref_el("transitionRef", t_id, f"tref_{t_id}"),
                id=instr_id,
            ))
            prev_transition_id = t_id

        pnml_el = mx.E(
            "pnml",
            *mx.global_content(new_uuid()),
            *places,
            *transitions,
            *arcs,
            id=pnml_id,
        )

        # materialTemplate: リンクされたItem 1件につき1つ (無ければ汎用1つ)
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

        cond_tmpl_id = f"condtmpl_exp{exp.elab_id}"
        cond_props = [_property_from_pv(p) for p in exp.condition_properties]
        condition_template = mx.E(
            "conditionTemplate",
            *mx.global_content(new_uuid(), properties=cond_props),
            mx.ref_el("placeRef", p_condition_in, "pref_cond"),
            id=cond_tmpl_id,
        )

        res_tmpl_id = f"restmpl_exp{exp.elab_id}"
        result_template = mx.E(
            "resultTemplate",
            *mx.global_content(new_uuid()),
            mx.ref_el("placeRef", p_result_out, "pref_res"),
            id=res_tmpl_id,
        )

        program_el = mx.E(
            "program",
            *mx.global_content(new_uuid()),
            *instructions,
            *material_templates,
            condition_template,
            result_template,
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
                material_template_ids, cond_tmpl_id, res_tmpl_id)

    # -- data ---------------------------------------------------------------

    def _build_data(self, exp: ExperimentData, material_template_ids, cond_tmpl_id, res_tmpl_id):
        results_id = f"results_exp{exp.elab_id}"

        materials = exp.materials or [LinkedItem(elab_id=0, title="(no linked item)")]
        material_instances = []
        for item, tmpl_id in zip(materials, material_template_ids):
            props = [_property_from_pv(p) for p in item.properties]
            material_instances.append(mx.E(
                "material",
                *mx.global_content(new_uuid(), description=item.title, properties=props),
                id=f"material_{item.elab_id}",
                ref=tmpl_id,
            ))

        cond_props = [_property_from_pv(p) for p in exp.condition_properties]
        condition_instance = mx.E(
            "condition",
            *mx.global_content(new_uuid(), properties=cond_props),
            id=f"condition_exp{exp.elab_id}",
            ref=cond_tmpl_id,
        )

        insertions = [
            mx.insertion_el(uri=u.uri, file_hash_b64=u.hash_b64, hash_method=u.hash_method, fmt=None)
            for u in exp.uploads
        ]
        result_props = [_property_from_pv(p) for p in exp.result_properties]
        result_instance = mx.E(
            "result",
            *mx.global_content(new_uuid(), insertions=insertions, properties=result_props),
            id=f"result_exp{exp.elab_id}",
            ref=res_tmpl_id,
        )

        results_el = mx.E(
            "results",
            *mx.global_content(new_uuid()),
            *material_instances,
            condition_instance,
            result_instance,
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
         material_template_ids, cond_tmpl_id, res_tmpl_id) = self._build_protocol(exp)
        data_el, results_id = self._build_data(exp, material_template_ids, cond_tmpl_id, res_tmpl_id)
        event_log_el = self._build_event_log(exp, method_id, program_id, step_instruction_ids, results_id)

        root.append(document_el)
        root.append(protocol_el)
        root.append(data_el)
        root.append(event_log_el)
        return root

    def to_bytes(self, exp: ExperimentData) -> bytes:
        root = self.build(exp)
        return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
