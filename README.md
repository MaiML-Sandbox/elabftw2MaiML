# elabftw2maiml

eLabFTW (REST API v2 / `elabapi-python`) の実験データを、JIS K 0200 (MaiML v1.0) 形式の
`.maiml` ファイルに変換するツールです。

**実験データの入力は eLabFTW の GUI（ブラウザ / `elabftw/desktop`）で行い、本ツールはその結果を
読み出して MaiML に変換するだけ**、という運用を想定しています（書き込みは行いません）。

同梱の `test_build_and_validate.py` で、合成データを使い実際に `schemas/maiml.xsd` に対して
スキーマ検証を行い、生成XMLが仕様に適合することを確認済みです。

```
python3 test_build_and_validate.py
# -> Schema valid: True
```

## 構成

```
elabftw2maiml/
  uuids.py           UUID生成 (v4乱数 / v5名前ベース)
  model.py           eLabFTW非依存の中間データモデル (ExperimentData 等)
  maiml_xml.py        MaiML要素の低レベル構築 (property/content/globalObjectContentGroup等)
  builder.py          ExperimentData -> <maiml> ルート要素の組み立て
  elabftw_client.py   elabapi-python でeLabFTWから取得 -> ExperimentDataへ変換
elabftw_to_maiml.py    CLIエントリポイント
test_build_and_validate.py  合成データでのビルド+XSD検証テスト（実行する場合、schemas/が必要）
```

## セットアップ

```bash
pip install elabapi-python lxml
export ELABFTW_HOST="https://elab.example.org/api/v2"
export ELABFTW_API_KEY="xxxxxxxxxxxxxxxxxxxx"     # eLabFTWのユーザー設定 > API keys で発行

python elabftw_to_maiml.py --experiment-id 123 --output experiment_123.maiml
```

`elabftw/desktop` でローカル起動している場合は `--host https://localhost:PORT/api/v2` のように
上書きしてください。

`--ns-prefix` / `--ns-uri` で、カスタムフィールドや試料情報のkey属性に使う名前空間を指定できます
(例: `--ns-prefix mylab --ns-uri https://mylab.example.org/maiml`)。省略時は仮の名前空間になるので、
実運用では必ず自組織の名前空間URIを指定してください。

## マッピング設計 (eLabFTW -> MaiML)

| eLabFTW側のデータ | MaiML側 | 備考 |
| --- | --- | --- |
| 実験 (Experiment) 1件 | `document` 1件 + `protocol/method` 1件 + `data` 1件 | `elab_id` を各id属性のsuffixに使用 |
| 実験オーナー (userid, fullname) | `document/owner` | `userid` から名前ベースUUID (v5) を生成。同一ユーザーは常に同一UUID |
| このツール自体 | `document/creator` | ツールのバージョン文字列から名前ベースUUIDを生成 |
| eLabFTW開発元 (Deltablot) | `document/vendor` | 固定の名前ベースUUID |
| 実験の作成日時 (`date`/`created_at`) | `document/date` | ISO8601に変換 |
| 実験のStep一覧 (Steps API, `ordering`順) | `protocol/method/pnml` の `transition` (直列に接続) + `program/instruction` | 1 Step = 1 transition = 1 instruction |
| リンクされたアイテム (`items_links`、詳細は `ItemsApi.get_item`) | `protocol/.../materialTemplate` + `data/.../material` | アイテムのExtra Fieldsを `property` に変換 |
| 実験のExtra Fields (`metadata.extra_fields`) | `protocol/.../conditionTemplate` + `data/.../condition` | 1つの `conditionTemplate`/`condition` にまとめて格納 |
| 実験本文 (`body`, HTMLタグ除去) / タグ (`tags`) | `data/.../result` の `property` | `resultTemplate`/`result` にまとめて格納 |
| 添付ファイル (`uploads`, ハッシュ値含む) | `data/.../result/insertion` | ダウンロードURL + ハッシュ値を参照として記録 (ファイル本体はMaiMLに埋め込まない) |
| 各Stepの開始/終了 (`finished_time`) | `eventLog/log/trace/event` (start/complete) | 最終Stepのcompleteイベントに `resultsRef` を付与 (仕様R-16準拠) |

### カスタムフィールドの型マッピング

| eLabFTWの `extra_fields[].type` | MaiMLの `xsi:type` |
| --- | --- |
| `number` | `doubleType` |
| `date` / `datetime-local` | `dateTimeType` |
| `checkbox` | `booleanType` |
| `url` | `uriType` |
| それ以外 (`text`, `select`, `radio`, `items`, `users` 等) | `stringType` |

## 既知の制約・今後の拡張ポイント

- **ペトリネットは単純な直列構造** (材料place → Step1 → Step2 → ... → 結果place) を機械的に生成します。
  分岐・並行工程がある実験は、この単純化では表現しきれないため、必要に応じて `builder.py` の
  `_build_protocol` を拡張してください。
- **`instrument` 要素は未使用**です。特定の計測装置をeLabFTW側で管理している場合 (例: リンクされた
  「装置」カテゴリのアイテム) は、`document/instrument` として追加実装することを推奨します。
- **`Signature` / `chain` / `parent` は未実装**です。すでにお持ちのXAdES-BES署名パッケージ・
  filehashパッケージと組み合わせて、生成した `.maiml` ファイルに後段で署名・チェーン情報を
  付与する運用を想定しています。
- **添付ファイルの秘匿化**が必要な場合は、既存のAES-256-GCM秘匿化パッケージを本ツールの出力に対して
  後段で適用してください（本ツール自体は秘匿禁止要素 [第1層タグ等] を秘匿しない前提で組み立てています）。
- **ダウンロードURLの形式** (`app/download.php?f=...&name=...`) はeLabFTWのバージョンにより異なる
  可能性があります。実環境に合わせて `elabftw_client.py` の `_fetch_uploads` を調整してください。
- 生成されたXMLはUUID (v4) がビルドの都度変わる第1層要素と、名前ベースUUID (v5) で固定される
  creator/owner/vendorが混在します。同一実験を複数回変換すると、`document`等のUUIDは毎回変わりますが、
  `owner`/`creator`/`vendor`のUUIDは常に同じ値になります (MaiML仕様 4.1節準拠)。
