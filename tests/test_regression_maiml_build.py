"""
回帰テスト: fetch_experiment() の結果 (ExperimentData) を MaimlBuilder に通して
生成される MaiML (.maiml) XML が、golden snapshot (tests/golden/*.maiml) と一致する
ことを確認する。

builder.py は乱数UUID (`uuids.new_uuid()`) をグローバル要素のUUIDとして多用しており、
実行ごとに異なるバイト列を生成するため、そのままでは golden 比較ができない。
本テストでは `elabftw2maiml.builder.new_uuid` を決定論的な採番関数に差し替えることで
出力を再現可能にしている。

生成されたMaiMLが `schemas/maiml.xsd` に対して妥当かどうかも合わせて検証する
(XSDファイルが本リポジトリに同梱されていない場合はスキップする -- README参照)。
"""
import itertools
from pathlib import Path

import pytest
from lxml import etree

from elabftw2maiml import MaimlBuilder, builder as builder_mod
from elabftw2maiml.elabftw_client import ElabftwClient

from .fixtures import FIXTURES, make_client

GOLDEN_DIR = Path(__file__).parent / "golden"
SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "maiml.xsd"


def _patch_deterministic_uuid(monkeypatch):
    """builder.new_uuid() を "00000000-0000-4000-8000-<連番12桁>" を返すように差し替える。
    named_uuid() (creator/owner/vendor/instrument用) はキー文字列に基づく決定論的なv5 UUIDの
    ため、そのままで再現可能であり差し替え不要。"""
    counter = itertools.count(1)
    monkeypatch.setattr(builder_mod, "new_uuid", lambda: f"00000000-0000-4000-8000-{next(counter):012d}")


def _build_experiment_data(fixture_name):
    builder_fn = FIXTURES[fixture_name]
    fake_experiment, raw_experiment_json, raw_items_json_by_id = builder_fn()
    client = make_client(ElabftwClient, fake_experiment, raw_experiment_json, raw_items_json_by_id)
    return client.fetch_experiment(fake_experiment.id, ns_prefix="ns1")


def _build_maiml_bytes(exp_data):
    b = MaimlBuilder(ns_prefix="ns1", ns_uri="https://example.org/maiml/mylab",
                      elab_host="elab.example.org")
    return b.to_bytes(exp_data)


@pytest.mark.parametrize("fixture_name", sorted(FIXTURES.keys()))
def test_maiml_build_matches_golden(fixture_name, monkeypatch):
    _patch_deterministic_uuid(monkeypatch)
    exp_data = _build_experiment_data(fixture_name)
    xml_bytes = _build_maiml_bytes(exp_data)

    golden_path = GOLDEN_DIR / f"{fixture_name}.maiml"
    expected = golden_path.read_bytes()

    assert xml_bytes == expected, (
        f"MaiML output for {fixture_name!r} no longer matches the golden snapshot "
        f"({golden_path}). If this change is intentional, regenerate the golden files "
        f"with tests/generate_golden.py and review the diff carefully."
    )


@pytest.mark.parametrize("fixture_name", sorted(FIXTURES.keys()))
def test_maiml_build_is_well_formed_xml(fixture_name):
    exp_data = _build_experiment_data(fixture_name)
    xml_bytes = _build_maiml_bytes(exp_data)
    # 構文的に妥当なXMLであることの確認 (壊れていればここで例外が送出される)
    etree.fromstring(xml_bytes)


@pytest.mark.parametrize("fixture_name", sorted(FIXTURES.keys()))
def test_maiml_build_validates_against_xsd(fixture_name):
    if not SCHEMA_PATH.exists():
        pytest.skip(
            f"{SCHEMA_PATH} が見つからないため、XSDによるスキーマ検証をスキップします。"
            "MaiML-Schema-1_0 のXSDを入手し、リポジトリ直下の schemas/maiml.xsd として"
            "配置すると、この検証が有効になります。"
        )
    schema = etree.XMLSchema(etree.parse(str(SCHEMA_PATH)))

    exp_data = _build_experiment_data(fixture_name)
    xml_bytes = _build_maiml_bytes(exp_data)
    doc = etree.fromstring(xml_bytes)

    ok = schema.validate(doc)
    if not ok:
        errors = "\n".join(str(e) for e in schema.error_log)
        pytest.fail(f"{fixture_name} produced MaiML that fails XSD validation:\n{errors}")
