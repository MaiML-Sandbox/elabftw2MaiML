"""
golden snapshot (tests/golden/) の再生成スクリプト。

**通常のテスト実行では使用しない。** 判定ロジックや MaiML の組み立てロジックを
意図的に変更し、その新しい出力を今後の回帰テストの基準として採用したい場合にのみ、
変更内容を十分にレビューした上で実行すること。

使い方:
    python -m tests.generate_golden

(プロジェクトルート = elabftw2maiml/ の親ディレクトリ から実行する)
"""
import itertools
import json
from pathlib import Path

from elabftw2maiml import MaimlBuilder, builder as builder_mod
from elabftw2maiml.elabftw_client import ElabftwClient

from .fixtures import FIXTURES, make_client
from .serialize import to_plain

GOLDEN_DIR = Path(__file__).parent / "golden"


def main():
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)

    for name, builder_fn in FIXTURES.items():
        fake_experiment, raw_experiment_json, raw_items_json_by_id = builder_fn()
        client = make_client(ElabftwClient, fake_experiment, raw_experiment_json, raw_items_json_by_id)
        exp_data = client.fetch_experiment(fake_experiment.id, ns_prefix="ns1")

        # -- ExperimentData snapshot ---------------------------------------
        plain = to_plain(exp_data)
        json_path = GOLDEN_DIR / f"experiment_data_{name}.json"
        json_path.write_text(
            json.dumps(plain, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {json_path}")

        # -- MaiML snapshot (決定論的UUIDに差し替えて生成) --------------------
        counter = itertools.count(1)
        original_new_uuid = builder_mod.new_uuid
        builder_mod.new_uuid = lambda: f"00000000-0000-4000-8000-{next(counter):012d}"
        try:
            b = MaimlBuilder(ns_prefix="ns1", ns_uri="https://example.org/maiml/mylab",
                              elab_host="elab.example.org")
            xml_bytes = b.to_bytes(exp_data)
        finally:
            builder_mod.new_uuid = original_new_uuid

        maiml_path = GOLDEN_DIR / f"{name}.maiml"
        maiml_path.write_bytes(xml_bytes)
        print(f"wrote {maiml_path}")


if __name__ == "__main__":
    main()
