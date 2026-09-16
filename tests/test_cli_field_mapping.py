"""`elabftw_to_maiml.py` CLIの `--field-mapping` オプション (Phase 5-3) の
統合テスト。

実際のeLabFTWサーバー・ネットワークには接続せず、`tests.fixtures` の合成データを
使って `ElabftwClient` を差し替え、CLI全体 (`main()`) が例外無く実行でき、
指定した対応表に基づくレポートが標準出力に表示され、`.maiml` ファイルが
実際に出力されることを確認する。

`--field-mapping` を指定しない既存の使い方には一切影響しないことは、
`tests/test_regression_maiml_build.py` 等の既存の回帰テストがCLIを経由せず
`ElabftwClient.fetch_experiment()`/`MaimlBuilder`を直接呼んでいるため、
別途保証されている (このテストファイルはオプション指定時の新しい経路のみを対象とする)。
"""
import sys

import elabftw_to_maiml as cli_module
from elabftw2maiml.elabftw_client import ElabftwClient

from .fixtures import FIXTURES, make_client

_FIELD_MAPPING_YAML = """
version: 1
fields:
  Resolution:
    semantic_type: resolution
    role: condition
    context: experiment
    target: condition_properties
"""


def test_field_mapping_option_runs_end_to_end_and_writes_output(tmp_path, monkeypatch, capsys):
    fake_experiment, raw_experiment_json, raw_items_json_by_id = FIXTURES["fixture_a"]()
    fixture_client = make_client(ElabftwClient, fake_experiment, raw_experiment_json, raw_items_json_by_id)

    # main() 内の `ElabftwClient(host_url=..., api_key=..., verify_ssl=...)` を、
    # 実際のネットワーク接続を行わないフィクスチャ由来のクライアントに差し替える。
    monkeypatch.setattr(cli_module, "ElabftwClient", lambda **kwargs: fixture_client)

    mapping_path = tmp_path / "mapping.yaml"
    mapping_path.write_text(_FIELD_MAPPING_YAML, encoding="utf-8")
    output_path = tmp_path / "out.maiml"

    argv = [
        "elabftw_to_maiml.py",
        "--experiment-id", str(fake_experiment.id),
        "--host", "https://elab.example.org/api/v2",
        "--api-key", "dummy-key",
        "--output", str(output_path),
        "--field-mapping", str(mapping_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    exit_code = cli_module.main()

    assert exit_code == 0
    assert output_path.exists()
    assert output_path.read_bytes().startswith(b"<?xml")

    out = capsys.readouterr().out
    assert "対応表" in out
    assert "resolution" in out
    # fixture_aのResolution=4.00は数値フィールドなので反映されるはず。
    assert "反映" in out


def test_field_mapping_option_reports_unmapped_fields(tmp_path, monkeypatch, capsys):
    """対応表に定義の無いフィールドが、黙って消えずに報告されることを確認する。"""
    fake_experiment, raw_experiment_json, raw_items_json_by_id = FIXTURES["fixture_a"]()
    fixture_client = make_client(ElabftwClient, fake_experiment, raw_experiment_json, raw_items_json_by_id)
    monkeypatch.setattr(cli_module, "ElabftwClient", lambda **kwargs: fixture_client)

    # fixture_aには "Scans" 等、この対応表に無いフィールドが複数存在する。
    mapping_path = tmp_path / "mapping.yaml"
    mapping_path.write_text(_FIELD_MAPPING_YAML, encoding="utf-8")
    output_path = tmp_path / "out.maiml"

    argv = [
        "elabftw_to_maiml.py",
        "--experiment-id", str(fake_experiment.id),
        "--host", "https://elab.example.org/api/v2",
        "--api-key", "dummy-key",
        "--output", str(output_path),
        "--field-mapping", str(mapping_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    cli_module.main()

    out = capsys.readouterr().out
    assert "対応表に定義が無いため無視されたフィールド" in out
    assert "Scans" in out


def test_without_field_mapping_option_behaves_as_before(tmp_path, monkeypatch, capsys):
    """--field-mapping を指定しない場合、対応表関連のレポートが一切出力されないこと
    (既存の挙動が変わっていないこと) を確認する。"""
    fake_experiment, raw_experiment_json, raw_items_json_by_id = FIXTURES["fixture_a"]()
    fixture_client = make_client(ElabftwClient, fake_experiment, raw_experiment_json, raw_items_json_by_id)
    monkeypatch.setattr(cli_module, "ElabftwClient", lambda **kwargs: fixture_client)

    output_path = tmp_path / "out.maiml"
    argv = [
        "elabftw_to_maiml.py",
        "--experiment-id", str(fake_experiment.id),
        "--host", "https://elab.example.org/api/v2",
        "--api-key", "dummy-key",
        "--output", str(output_path),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    exit_code = cli_module.main()

    assert exit_code == 0
    assert output_path.exists()
    out = capsys.readouterr().out
    assert "対応表" not in out
