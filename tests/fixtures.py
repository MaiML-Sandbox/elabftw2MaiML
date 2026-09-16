"""
回帰テスト用の合成 (synthetic) eLabFTW APIレスポンス フィクスチャ。

`elabapi_python` の実際のSDKモデルは使わず、`ElabftwClient` が実際に参照する
属性 (userid/fullname/items_links/steps/uploads/tags/created_at/body 等) だけを
持つ `SimpleNamespace` で代用する。これにより、実際の eLabFTW サーバーに接続
せずに `fetch_experiment()` のロジック (構造情報からの役割判定・材料/条件/結果の
組み立て等) を検証できる。

各 build_fixture_*() は (fake_experiment, raw_experiment_json, raw_items_json_by_id)
のタプルを返す。raw_experiment_json / raw_items_json_by_id は
`ElabftwClient._get_raw_json()` が返す生JSON (dict) を模したもの。
"""
from __future__ import annotations

import re
from types import SimpleNamespace

BASE_URL = "https://elab.example.org"


def make_client(client_cls, fake_experiment, raw_experiment_json, raw_items_json_by_id,
                 base_url: str = BASE_URL):
    """
    実際の `__init__` (elabapi_python.Configuration/ApiClient の構築) を経由せず、
    `experiments_api.get_experiment()` と `_get_raw_json()` だけをフィクスチャで
    差し替えた `ElabftwClient` (またはそのbaseline版) インスタンスを作る。

    `client_cls` は `elabftw2maiml.elabftw_client.ElabftwClient` あるいは
    baseline (リファクタリング前) 版のいずれでも良い。
    """
    client = client_cls.__new__(client_cls)
    client.host_url = f"{base_url}/api/v2"
    client.base_url = base_url

    # リファクタリング後の版は self._structured_interpreter を参照するため、
    # 存在すれば設定しておく (baseline版には無関係な属性なので無害)。
    try:
        from elabftw2maiml.interpretation import StructuredRuleInterpreter
        client._structured_interpreter = StructuredRuleInterpreter()
    except Exception:
        pass

    client.experiments_api = SimpleNamespace(get_experiment=lambda eid: fake_experiment)

    def _get_raw_json(resource_path):
        if resource_path == f"/experiments/{fake_experiment.id}":
            return raw_experiment_json
        m = re.match(r"^/items/(\d+)$", resource_path)
        if m:
            return raw_items_json_by_id.get(int(m.group(1)))
        return None

    client._get_raw_json = _get_raw_json
    return client


def build_fixture_a():
    """
    「豊富なケース」フィクスチャ。以下を一度に網羅する:
      - Category によるリンクアイテムの役割判定 (material/instrument/result)
      - Tag によるリンクアイテムの役割判定 (condition)
      - Custom Field Group によるロール判定 (MATERIAL/RESULT グループ、
        グループ無しのCONDITIONフォールバック)
      - カスタムフィールドからの creator/vendor 抽出
      - ネストしたリンクアイテムの再帰的解決 (instrument -> instrument)
      - Steps、添付ファイルの変換
    """
    fake_experiment = SimpleNamespace(
        id=123,
        title="PMMA薄膜のFT-IR測定",
        userid=42,
        fullname="Yamada Taro",
        created_at="2026-07-20 09:00:00",
        body="<p>PMMA薄膜サンプルのFT-IRスペクトルを測定した。</p>",
        tags="FTIR,polymer",
        items_links=[
            SimpleNamespace(entityid=88, title="PMMAサンプル", category_title="Samples"),
            SimpleNamespace(entityid=90, title="IRAffinity-1S", category_title="Resources"),
            SimpleNamespace(entityid=91, title="測定条件セット", category_title=None),
            SimpleNamespace(entityid=92, title="分析結果まとめ", category_title="Results"),
        ],
        steps=[
            SimpleNamespace(id=501, ordering=1, body="<p>サンプル準備</p>",
                             finished=True, finished_time="2026-07-20 09:20:00"),
            SimpleNamespace(id=502, ordering=2, body="<p>FT-IR測定</p>",
                             finished=True, finished_time="2026-07-20 09:45:00"),
        ],
        uploads=[
            SimpleNamespace(
                real_name="spectrum.csv",
                long_name="abc123_spectrum.csv",
                hash="deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                hash_algorithm="sha256",
            ),
        ],
    )

    raw_experiment_json = {
        "metadata": {
            "extra_fields": {
                "Resolution": {"type": "number", "value": "4.00", "description": None, "group_id": 10},
                "Scans": {"type": "number", "value": "32", "description": None, "group_id": None},
                "SampleNote": {"type": "text", "value": "室温保管", "description": None, "group_id": 11},
                "AnalysisSummary": {"type": "textarea", "value": "良好な吸収ピークを確認",
                                     "description": None, "group_id": 12},
                "使用装置": {"type": "text", "value": "FT-IR IRAffinity-1S", "description": None, "group_id": None},
                "装置メーカー": {"type": "text", "value": "Shimadzu", "description": None, "group_id": None},
            },
            "elabftw": {
                "extra_fields_groups": [
                    {"id": 10, "name": "CONDITION"},
                    {"id": 11, "name": "MATERIAL"},
                    {"id": 12, "name": "RESULT"},
                ],
            },
        },
    }

    raw_items_json_by_id = {
        88: {
            "metadata": {
                "extra_fields": {
                    "SampleID": {"type": "text", "value": "SA-001", "description": None, "group_id": None},
                    "Thickness": {"type": "number", "value": "0.125", "description": None, "group_id": None},
                },
            },
            "tags": None,
            "items_links": [],
        },
        90: {
            "metadata": {},
            "tags": None,
            "items_links": [
                {"entityid": 95, "title": "検出器ユニット", "category_title": "Equipment"},
            ],
        },
        91: {
            "metadata": {
                "extra_fields": {
                    "Humidity": {"type": "number", "value": "45", "description": None, "group_id": None},
                },
            },
            "tags": "条件,予備",
            "items_links": [],
        },
        92: {
            "metadata": {},
            "tags": None,
            "items_links": [],
        },
        95: {
            "metadata": {},
            "tags": None,
            "items_links": [],
        },
    }

    return fake_experiment, raw_experiment_json, raw_items_json_by_id


def build_fixture_b():
    """
    「最小/既定値」フィクスチャ。リンクアイテム・カスタムフィールド・添付ファイルが
    一切無い場合に、警告のみでクラッシュせず、材料/条件/結果が空リストになり、
    creator/vendor/instrument が None/空のまま (builder.py 側のフォールバックに委ねる)
    ことを確認する。
    """
    fake_experiment = SimpleNamespace(
        id=200,
        title="汎用天秤による秤量",
        userid=7,
        fullname="Suzuki Hanako",
        created_at="2026-08-01 10:00:00",
        body=None,
        tags=None,
        items_links=[],
        steps=[
            SimpleNamespace(id=701, ordering=1, body="<p>秤量</p>",
                             finished=True, finished_time="2026-08-01 10:05:00"),
        ],
        uploads=[],
    )
    raw_experiment_json = {"metadata": {}}
    raw_items_json_by_id = {}
    return fake_experiment, raw_experiment_json, raw_items_json_by_id


FIXTURES = {
    "fixture_a": build_fixture_a,
    "fixture_b": build_fixture_b,
}
