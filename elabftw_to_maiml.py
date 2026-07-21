#!/usr/bin/env python3
"""
eLabFTWの実験データをMaiML(.maiml)ファイルに変換するCLIツール。

使い方:
    export ELABFTW_HOST="https://elab.example.org/api/v2"
    export ELABFTW_API_KEY="xxxxxxxxxxxxxxxxxxxx"
    python elabftw_to_maiml.py --experiment-id 123 --output out.maiml

    # elabftw/desktop でローカル起動している場合は --host で上書き可
    python elabftw_to_maiml.py --experiment-id 123 \\
        --host https://localhost:PORT/api/v2 --api-key xxxx --output out.maiml

    # 名前空間 (property/content の key 属性プレフィックス) を指定
    python elabftw_to_maiml.py --experiment-id 123 \\
        --ns-prefix mylab --ns-uri https://mylab.example.org/maiml --output out.maiml
"""
from __future__ import annotations

import argparse
import os
import sys

from elabftw2maiml import MaimlBuilder
from elabftw2maiml.elabftw_client import ElabftwClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--experiment-id", type=int, required=True, help="eLabFTWの実験ID")
    parser.add_argument("--host", default=os.environ.get("ELABFTW_HOST"),
                         help="eLabFTW APIのベースURL (例: https://elab.example.org/api/v2)。"
                              "既定値は環境変数 ELABFTW_HOST")
    parser.add_argument("--api-key", default=os.environ.get("ELABFTW_API_KEY"),
                         help="eLabFTWのAPIキー。既定値は環境変数 ELABFTW_API_KEY")
    parser.add_argument("--output", "-o", required=True, help="出力する .maiml ファイルのパス")
    parser.add_argument("--ns-prefix", default="ns1", help="property/content の key 属性に使う名前空間プレフィックス")
    parser.add_argument("--ns-uri", default="https://example.org/maiml/mylab",
                         help="ns-prefix に対応する名前空間URI")
    parser.add_argument("--insecure", action="store_true", help="TLS証明書検証を無効化する (自己署名証明書の開発環境向け)")
    args = parser.parse_args()

    if not args.host or not args.api_key:
        parser.error("--host/--api-key (または環境変数 ELABFTW_HOST/ELABFTW_API_KEY) が必要です")

    client = ElabftwClient(host_url=args.host, api_key=args.api_key, verify_ssl=not args.insecure)
    exp_data = client.fetch_experiment(args.experiment_id, ns_prefix=args.ns_prefix)

    builder = MaimlBuilder(ns_prefix=args.ns_prefix, ns_uri=args.ns_uri,
                            elab_host=client.base_url)
    xml_bytes = builder.to_bytes(exp_data)

    with open(args.output, "wb") as f:
        f.write(xml_bytes)

    print(f"実験 #{args.experiment_id} ({exp_data.title!r}) を {args.output} に出力しました "
          f"({len(xml_bytes)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
