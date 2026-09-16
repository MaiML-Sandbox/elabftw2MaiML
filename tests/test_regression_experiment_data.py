"""
回帰テスト: ElabftwClient.fetch_experiment() が返す ExperimentData が、
リファクタリング前 (interpretation/ 分離前) に固定した golden snapshot
(tests/golden/experiment_data_*.json) と一致することを確認する。

これは elabftw2MaiML_phase1_development_plan.md の「第一弾」で要求されている
回帰テストの中核: Group/Tag/Category による役割判定、Custom Fieldの型変換、
material/condition/result の生成、Stepsの変換、添付ファイルの扱いが
判定ロジックの分離 (StructuredRuleInterpreter への移設) によって変化して
いないことを保証する。

golden snapshotは tests/generate_golden.py で (意図的な仕様変更があった場合のみ)
再生成できる。
"""
import json
from pathlib import Path

import pytest

from elabftw2maiml.elabftw_client import ElabftwClient

from .fixtures import FIXTURES, make_client
from .serialize import to_plain

GOLDEN_DIR = Path(__file__).parent / "golden"


@pytest.mark.parametrize("fixture_name", sorted(FIXTURES.keys()))
def test_fetch_experiment_matches_golden(fixture_name):
    builder = FIXTURES[fixture_name]
    fake_experiment, raw_experiment_json, raw_items_json_by_id = builder()

    client = make_client(ElabftwClient, fake_experiment, raw_experiment_json, raw_items_json_by_id)
    exp_data = client.fetch_experiment(fake_experiment.id, ns_prefix="ns1")
    actual = to_plain(exp_data)

    golden_path = GOLDEN_DIR / f"experiment_data_{fixture_name}.json"
    expected = json.loads(golden_path.read_text(encoding="utf-8"))

    assert actual == expected, (
        f"ExperimentData for {fixture_name!r} no longer matches the golden snapshot "
        f"({golden_path}). If this change is intentional, regenerate the golden files "
        f"with tests/generate_golden.py and review the diff carefully."
    )
