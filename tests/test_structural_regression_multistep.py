"""
データモデル対称化改修 (elabftw2MaiML_model_refactoring_plan.md) の目的そのものを
構造的に検証する回帰テスト。

`ExperimentData` -> `MaimlBuilder.build()` を実際のMaiML XML (lxmlツリー) まで通し、
以下のシナリオで期待どおりの構造になっていることを確認する:

    Step 1
     ├─ Material A (materials)
     │   └─ property (sample_name)
     ├─ Condition A (conditions, context=step:1)
     │   └─ property (temperature=40degC)
     └─ Result A (results, context=step:1)
         └─ property (signal_intensity=123)

    Step 2
     ├─ Condition B (conditions, context=step:2)
     │   └─ property (temperature=80degC)
     └─ Result B (results, context=step:2)
         └─ property (signal_intensity=456)

確認する観点:
    1. Materialが正しいmaterialTemplate/materialへ入り、Condition/Resultの
       propertyと混ざらないこと。
    2. Condition A/Bが別々のConditionData/conditionTemplateとして保持され、
       混ざらないこと (ただしPNML上のplaceは現状どちらも共有のp_condition_in
       であり、Step単位のplace分離は今回のスコープ外。README/CHANGELOG参照)。
    3. ResultがStepごとに正しいresultTemplate/resultへ分かれること
       (Result AはStep1側にのみ、Result BはStep2側にのみ現れる)。
    4. 同じsemantic_type ("temperature"/"signal_intensity") でもcontext
       (Step) が違えば、どちらも欠落せずに両方反映されること。
    5. 生成されたXML全体で、`ref`/`source`/`target`属性 (placeRef/templateRef/
       instanceRef/transitionRef/*Ref、arcのsource・target等) が指す`id`が実際に
       存在すること (参照切れが無いこと)。
"""
from datetime import datetime
from decimal import Decimal

from lxml import etree

from elabftw2maiml.model import ExperimentData, MaterialData, PropertyValue, Step
from elabftw2maiml.builder import MaimlBuilder
from elabftw2maiml.interpretation.conflict import InterpretationCandidate
from elabftw2maiml.interpretation.pipeline import InterpretationReport, step_context
from elabftw2maiml.interpretation.apply import apply_interpretation_report

NS = {"m": "http://www.maiml.org/schemas"}
_REF_ATTRS = ("ref", "source", "target")


def _condition_candidate(value, step_elab_id):
    return InterpretationCandidate(
        semantic_type="temperature",
        value=value,
        source="custom_field",
        confidence=1.0,
        unit="degC",
        context=step_context(step_elab_id),
        role="condition",
        target="conditions",
    )


def _result_candidate(value, step_elab_id):
    return InterpretationCandidate(
        semantic_type="signal_intensity",
        value=value,
        source="custom_field",
        confidence=1.0,
        unit=None,
        context=step_context(step_elab_id),
        role="result",
        target="results",
    )


def _build_experiment() -> ExperimentData:
    experiment = ExperimentData(
        elab_id=99,
        title="構造回帰テスト (Step1: Material+Condition+Result / Step2: Condition+Result)",
        date=datetime(2026, 9, 29),
        steps=[
            Step(elab_id=1, title="Step 1"),
            Step(elab_id=2, title="Step 2"),
        ],
    )
    # Material A: elabftw_client.py がリンクされたアイテムから組み立てるのと同じ形
    # (実際のクライアント経由ではなく、テストのため直接構築する)。
    experiment.materials.append(MaterialData(
        key="material:item:501",
        elab_id=501,
        title="Sample A",
        properties=[
            PropertyValue(key="ns1:sample_name", xsi_type="stringType", value="Sample-A"),
        ],
    ))

    report = InterpretationReport(accepted=[
        _condition_candidate(Decimal("40"), 1),
        _condition_candidate(Decimal("80"), 2),
        _result_candidate(Decimal("123"), 1),
        _result_candidate(Decimal("456"), 2),
    ])
    apply_interpretation_report(experiment, report)
    return experiment


def _build_xml(experiment: ExperimentData) -> etree._Element:
    builder = MaimlBuilder(ns_prefix="ns1", ns_uri="https://example.org/maiml/mylab",
                            elab_host="elab.example.org")
    return builder.build(experiment)


def _prop_value(prop_el) -> str:
    value_el = prop_el.find("m:value", NS)
    assert value_el is not None, f"property (key={prop_el.get('key')}) に <value> がありません"
    return value_el.text


def _collect_ids(root) -> set:
    return {el.get("id") for el in root.iter() if el.get("id") is not None}


def _dangling_references(root) -> list:
    """ref/source/target属性が指すidが、文書内に実在するidの集合に含まれない
    要素を洗い出す (placeRef/templateRef/instanceRef/transitionRef/*Ref、および
    arcのsource/target、material/condition/resultインスタンスのref属性を含む)。"""
    ids = _collect_ids(root)
    dangling = []
    for el in root.iter():
        tag = etree.QName(el).localname
        for attr in _REF_ATTRS:
            val = el.get(attr)
            if val is not None and val not in ids:
                dangling.append((tag, attr, val))
    return dangling


class TestExperimentDataFixture:
    """反映後のExperimentData自体が、意図した形になっていることの確認
    (XML化する前の段階での検証)。"""

    def test_single_material_with_correct_property(self):
        experiment = _build_experiment()
        assert len(experiment.materials) == 1
        material = experiment.materials[0]
        assert material.key == "material:item:501"
        prop_keys = {p.key: p.value for p in material.properties}
        assert prop_keys == {"ns1:sample_name": "Sample-A"}

    def test_two_separate_conditions_not_merged(self):
        experiment = _build_experiment()
        assert len(experiment.conditions) == 2
        by_key = {c.key: c for c in experiment.conditions}
        assert set(by_key) == {"condition:step:1", "condition:step:2"}
        # 同じsemantic_type (temperature) 由来のproperty keyは両方とも
        # "ns1:temperature" だが、別オブジェクトなので混ざらず、値も別々。
        assert [p.key for p in by_key["condition:step:1"].properties] == ["ns1:temperature"]
        assert [p.key for p in by_key["condition:step:2"].properties] == ["ns1:temperature"]
        assert by_key["condition:step:1"].properties[0].value == Decimal("40")
        assert by_key["condition:step:2"].properties[0].value == Decimal("80")
        assert by_key["condition:step:1"].step_id == 1
        assert by_key["condition:step:2"].step_id == 2

    def test_two_separate_results_scoped_to_their_own_step(self):
        experiment = _build_experiment()
        assert len(experiment.results) == 2
        by_key = {r.key: r for r in experiment.results}
        assert set(by_key) == {"result:step:1", "result:step:2"}
        assert by_key["result:step:1"].step_id == 1
        assert by_key["result:step:2"].step_id == 2
        assert by_key["result:step:1"].properties[0].value == Decimal("123")
        assert by_key["result:step:2"].properties[0].value == Decimal("456")


class TestBuiltXmlStructure:
    """`MaimlBuilder.build()` が生成したXMLツリー自体の構造検証。"""

    def test_material_goes_into_its_own_template_and_instance_only(self):
        experiment = _build_experiment()
        root = _build_xml(experiment)

        mat_templates = root.findall(".//m:materialTemplate", NS)
        assert len(mat_templates) == 1
        mat_tmpl = mat_templates[0]
        assert mat_tmpl.get("id") == "mattmpl_material_item_501"
        place_ref = mat_tmpl.find("m:placeRef", NS)
        assert place_ref is not None
        assert place_ref.get("ref") == "p_material_in"

        mat_props = mat_tmpl.findall("m:property", NS)
        assert [p.get("key") for p in mat_props] == ["ns1:sample_name"]
        assert _prop_value(mat_props[0]) == "Sample-A"

        mat_instances = root.findall(".//m:results/m:material", NS)
        assert len(mat_instances) == 1
        assert mat_instances[0].get("ref") == mat_tmpl.get("id")
        assert [p.get("key") for p in mat_instances[0].findall("m:property", NS)] == ["ns1:sample_name"]

    def test_two_condition_templates_kept_separate_with_correct_values(self):
        experiment = _build_experiment()
        root = _build_xml(experiment)

        cond_templates = root.findall(".//m:conditionTemplate", NS)
        assert len(cond_templates) == 2
        ids = {t.get("id") for t in cond_templates}
        assert ids == {"condtmpl_condition_step_1", "condtmpl_condition_step_2"}

        by_id = {t.get("id"): t for t in cond_templates}
        step1_props = by_id["condtmpl_condition_step_1"].findall("m:property", NS)
        step2_props = by_id["condtmpl_condition_step_2"].findall("m:property", NS)
        assert len(step1_props) == 1 and len(step2_props) == 1
        assert _prop_value(step1_props[0]) == "40"
        assert _prop_value(step2_props[0]) == "80"
        # 現状はどちらも共有のp_condition_inへ接続する (Step単位のPNML経路分離は
        # 今回のスコープ外。README「既知の制約・今後の拡張ポイント」参照)。
        for tmpl in cond_templates:
            place_ref = tmpl.find("m:placeRef", NS)
            assert place_ref.get("ref") == "p_condition_in"

        cond_instances = root.findall(".//m:results/m:condition", NS)
        assert len(cond_instances) == 2
        instance_values = {inst.get("ref"): _prop_value(inst.findall("m:property", NS)[0])
                            for inst in cond_instances}
        assert instance_values == {
            "condtmpl_condition_step_1": "40",
            "condtmpl_condition_step_2": "80",
        }

    def test_results_are_split_one_template_per_step_without_cross_contamination(self):
        experiment = _build_experiment()
        root = _build_xml(experiment)

        res_templates = root.findall(".//m:program/m:resultTemplate", NS)
        assert len(res_templates) == 2  # Step1/Step2それぞれに1つずつ
        by_id = {t.get("id"): t for t in res_templates}
        assert set(by_id) == {"restmpl_step_1", "restmpl_step_2"}

        step1_tmpl = by_id["restmpl_step_1"]
        step2_tmpl = by_id["restmpl_step_2"]

        # Result Aの値(123)はStep1側のみ、Result Bの値(456)はStep2側のみに現れる
        # (お互いのpropertyが混入していないこと)。
        assert [p.get("key") for p in step1_tmpl.findall("m:property", NS)] == ["ns1:signal_intensity"]
        assert _prop_value(step1_tmpl.findall("m:property", NS)[0]) == "123"
        assert [p.get("key") for p in step2_tmpl.findall("m:property", NS)] == ["ns1:signal_intensity"]
        assert _prop_value(step2_tmpl.findall("m:property", NS)[0]) == "456"

        # 最初のSTEPのresultTemplateはtemplateRefを持たない
        # (PNML上のarc+共有placeRefで既に材料/条件からの入力を表現済みのため)。
        assert step1_tmpl.find("m:templateRef", NS) is None
        # 2番目 (=最後) のSTEPは直前のSTEPのresultTemplateをtemplateRefで参照する。
        template_ref = step2_tmpl.find("m:templateRef", NS)
        assert template_ref is not None
        assert template_ref.get("ref") == "restmpl_step_1"

        # placeRef: Step1(途中扱い)はp_mid_1、Step2(最後)はp_result_outへ接続。
        assert step1_tmpl.find("m:placeRef", NS).get("ref") == "p_mid_1"
        assert step2_tmpl.find("m:placeRef", NS).get("ref") == "p_result_out"

        # data側のresultインスタンスも同じ接続パターンをinstanceRefで反映する。
        res_instances = root.findall(".//m:results/m:result", NS)
        assert len(res_instances) == 2
        by_ref = {inst.get("ref"): inst for inst in res_instances}
        assert set(by_ref) == {"restmpl_step_1", "restmpl_step_2"}

        step1_inst = by_ref["restmpl_step_1"]
        step2_inst = by_ref["restmpl_step_2"]
        assert _prop_value(step1_inst.findall("m:property", NS)[0]) == "123"
        assert _prop_value(step2_inst.findall("m:property", NS)[0]) == "456"
        assert step1_inst.find("m:instanceRef", NS) is None
        instance_ref = step2_inst.find("m:instanceRef", NS)
        assert instance_ref is not None
        assert instance_ref.get("ref") == step1_inst.get("id")

    def test_pnml_has_two_transitions_and_expected_places(self):
        experiment = _build_experiment()
        root = _build_xml(experiment)

        transitions = root.findall(".//m:pnml/m:transition", NS)
        assert {t.get("id") for t in transitions} == {"t_step_1", "t_step_2"}

        places = root.findall(".//m:pnml/m:place", NS)
        assert {p.get("id") for p in places} == {
            "p_material_in", "p_condition_in", "p_mid_1", "p_result_out",
        }

    def test_no_dangling_ref_source_or_target_attributes(self):
        """placeRef/templateRef/instanceRef/transitionRef/*Ref、およびarcの
        source/targetを含め、文書内の全ての参照が実在するidを指していること。"""
        experiment = _build_experiment()
        root = _build_xml(experiment)

        dangling = _dangling_references(root)
        assert dangling == []

    def test_generated_xml_is_well_formed(self):
        experiment = _build_experiment()
        builder = MaimlBuilder(ns_prefix="ns1", ns_uri="https://example.org/maiml/mylab",
                                elab_host="elab.example.org")
        xml_bytes = builder.to_bytes(experiment)
        root = etree.fromstring(xml_bytes)
        assert root is not None
