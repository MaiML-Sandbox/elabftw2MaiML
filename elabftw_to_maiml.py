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
    parser.add_argument("--creator-field", action="append", default=None,
                         help="creator(使用装置)として扱うカスタムフィールド名。複数指定可。"
                              "省略時は「使用装置」「Instrument」等の既定候補を使用")
    parser.add_argument("--vendor-field", action="append", default=None,
                         help="vendor(装置メーカー)として扱うカスタムフィールド名。複数指定可。"
                              "省略時は「装置メーカー」「Vendor」等の既定候補を使用")
    parser.add_argument("--instrument-category", action="append", default=None,
                         help="[非推奨・互換用] --role-category instrument=... と同じ")
    parser.add_argument("--instrument-tag", action="append", default=None,
                         help="[非推奨・互換用] --role-tag instrument=... と同じ")
    parser.add_argument("--role-category", action="append", default=None, metavar="ROLE=VALUE",
                         help="リンクされたアイテムをROLE (material/condition/result/creator/"
                              "instrument/vendor) として扱うカテゴリ名。'ROLE=値' の形式で複数指定可"
                              "(例: --role-category condition=測定条件 --role-category result=分析結果)。"
                              "指定したROLEのみ既定候補を上書きし、他のROLEは既定候補のまま")
    parser.add_argument("--role-tag", action="append", default=None, metavar="ROLE=VALUE",
                         help="リンクされたアイテムをROLEとして扱うタグ名。'ROLE=値' の形式で複数指定可。"
                              "指定方法は --role-category と同様")
    parser.add_argument("--field-group", action="append", default=None, metavar="ROLE=VALUE",
                         help="実験自身のカスタムフィールドを、eLabFTWの「フィールドグループ」機能"
                              "(CUSTOM FIELDS内のMATERIAL/CONDITION/RESULTのような折りたたみグループ)の"
                              "グループ名からROLE (material または result。conditionは既定のフォールバック"
                              "先なので指定不要) に振り分けるための候補文字列。'ROLE=値' の形式で複数指定可"
                              "(例: --field-group material=試料情報 --field-group result=解析結果)")
    parser.add_argument("--link-depth", type=int, default=2,
                         help="リンクされたアイテムをどこまで再帰的にたどるか (既定値: 2)。"
                              "1なら実験に直接リンクされたアイテムのみ、2なら「アイテムがさらに"
                              "リンクしている別アイテム」まで辿る。循環参照があっても無限ループにはならない")
    parser.add_argument("--field-mapping", default=None, metavar="PATH",
                         help="[Phase 5-2/5-3・任意] 実際のCustom Field名をsemantic_type/role/"
                              "unit/context/targetに対応付けるYAML設定ファイルのパス "
                              "(elabftw2maiml/interpretation/field_mappings/sem_tem_example.yaml が"
                              "書式の例)。指定すると、実験のExtra Fieldsと自由記述 (実験本文・Step本文)"
                              "を突き合わせ、食い違いが無い値だけを自動反映してMaiMLを生成する。"
                              "食い違いがある値・役割 (role) や反映先 (target) が確定しない値は"
                              "反映されず、標準出力にレポートとして表示される。省略時は従来通り"
                              "(このオプション自体が存在しなかった場合と全く同じ動作)")
    parser.add_argument("--confidence-threshold", type=float, default=1.0,
                         help="[--field-mapping指定時のみ有効] 候補を自動反映する際のconfidence"
                              "閾値 (既定値: 1.0)。自由記述からの抽出候補は既定でconfidence=0.95に"
                              "なるため、1.0のままだと自由記述側は (構造化フィールドと一致していても)"
                              "常にunclassifiedへの参考情報として残るだけになる")
    args = parser.parse_args()

    if not args.host or not args.api_key:
        parser.error("--host/--api-key (または環境変数 ELABFTW_HOST/ELABFTW_API_KEY) が必要です")

    def _parse_role_candidates(items, legacy_instrument_items):
        """'ROLE=VALUE' 形式の引数リストを {role: [value, ...]} にまとめる。
        [非推奨] --instrument-category/--instrument-tag もここでrole=instrumentとして合流させる。"""
        result: dict = {}
        for item in (items or []):
            if "=" not in item:
                parser.error(f"'{item}' は 'ROLE=値' の形式で指定してください (例: condition=測定条件)")
            role, _, value = item.partition("=")
            role = role.strip()
            if role not in ("material", "condition", "result", "creator", "instrument", "vendor"):
                parser.error(f"不明なROLE '{role}' です (material/condition/result/creator/"
                              f"instrument/vendor のいずれかを指定してください)")
            result.setdefault(role, []).append(value.strip())
        for value in (legacy_instrument_items or []):
            result.setdefault("instrument", []).append(value)
        return result or None

    def _parse_field_group_candidates(items):
        result: dict = {}
        for item in (items or []):
            if "=" not in item:
                parser.error(f"'{item}' は 'ROLE=値' の形式で指定してください (例: material=試料情報)")
            role, _, value = item.partition("=")
            role = role.strip()
            if role not in ("material", "result"):
                parser.error(f"--field-group で指定できるROLEは material/result のみです "
                              f"(condition は既定のフォールバック先です): '{role}'")
            result.setdefault(role, []).append(value.strip())
        return result or None

    role_category_candidates = _parse_role_candidates(args.role_category, args.instrument_category)
    role_tag_candidates = _parse_role_candidates(args.role_tag, args.instrument_tag)
    field_group_candidates = _parse_field_group_candidates(args.field_group)

    client = ElabftwClient(host_url=args.host, api_key=args.api_key, verify_ssl=not args.insecure)
    exp_data = client.fetch_experiment(
        args.experiment_id, ns_prefix=args.ns_prefix,
        creator_field_candidates=args.creator_field,
        vendor_field_candidates=args.vendor_field,
        role_category_candidates=role_category_candidates,
        role_tag_candidates=role_tag_candidates,
        field_group_candidates=field_group_candidates,
        link_depth=args.link_depth,
    )

    if args.field_mapping:
        from elabftw2maiml.interpretation import (
            FieldMapping,
            build_structured_candidates,
            find_missing_required_fields,
            InterpretationPipeline,
            apply_interpretation_report,
            format_interpretation_report,
            SemTemTextRuleInterpreter,
        )

        field_mapping = FieldMapping.from_yaml_file(args.field_mapping)
        raw_fields = client.fetch_raw_custom_fields(args.experiment_id)
        structured_candidates, unmapped_fields = build_structured_candidates(
            raw_fields, field_mapping, source="custom_field")
        missing_required_fields = find_missing_required_fields(raw_fields, field_mapping)

        pipeline = InterpretationPipeline(
            extra_text_interpreters=[SemTemTextRuleInterpreter()],
            confidence_threshold=args.confidence_threshold,
        )
        report = pipeline.interpret_experiment(exp_data, structured_candidates=structured_candidates)
        apply_logs = apply_interpretation_report(exp_data, report, ns_prefix=args.ns_prefix)

        print("\n--- 対応表 (--field-mapping) による解釈結果 -----------------------------")
        print(format_interpretation_report(report))
        if apply_logs:
            print("\n[ExperimentDataへの反映ログ]")
            for line in apply_logs:
                print(f"  {line}")
        if unmapped_fields:
            print("\n[対応表に定義が無いため無視されたフィールド]")
            for f in unmapped_fields:
                print(f"  - {f.name} (group={f.group})")
        if missing_required_fields:
            print("\n[警告: 対応表でrequired: trueと指定されているが、実験に存在しないフィールド]")
            for name in missing_required_fields:
                print(f"  - {name}")
        print("--------------------------------------------------------------------------\n")

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
