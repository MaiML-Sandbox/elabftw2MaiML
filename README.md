# elabftw2maiml

eLabFTW (REST API v2 / `elabapi-python`) の実験データを、JIS K 0200 (MaiML v1.0) 形式の
`.maiml` ファイルに変換するツールです。

> **本ツールは eLabFTW / Deltablot 社とは無関係な非公式ツールです。**
> eLabFTWおよびそのAPIはDeltablot社が開発するオープンソースソフトウェアであり、本ツールは
> それを利用する第三者スクリプトです。Deltablot社による公認・サポートは受けていません。


**実験データの入力は eLabFTW の GUI（ブラウザ / `elabftw/desktop`）で行い、本ツールはその結果を
読み出して MaiML に変換するだけ**、という運用を想定しています（書き込みは行いません）。

同梱の `test_build_and_validate.py` で、合成データを使い実際に `schemas/maiml.xsd` に対して
スキーマ検証を行い、生成XMLが仕様に適合することを確認済みです。

```
python3 test_build_and_validate.py
# -> Schema valid: True
```

## 対応するeLabFTWバージョン

- eLabFTW REST API v2 を使用します (API v1は対象外)。REST API v2は eLabFTW 4.0以降で
  利用可能ですが、`elabapi-python` パッケージ自体はeLabFTW側のスキーマ変更に合わせて
  バージョンが分かれています (例: `elabapi-python` 5.4.x は eLabFTW 5.4.x向け)。
- 本ツールの動作確認は `elabapi-python==5.6.0` (eLabFTW 5.6.x相当) で行っています。
- **お使いのeLabFTWサーバーのバージョンに近い `elabapi-python` を pip でインストールしてください**
  (例: `pip install elabapi-python==5.4.0`)。大きくバージョンがずれると、`Experiment`/`Step`等の
  モデルのフィールド構成が変わり、`elabftw_client.py` の一部が動作しない可能性があります。

## 必要なAPI権限

- 本ツールは**読み取り専用**です (eLabFTWへの書き込み・更新は一切行いません)。
- eLabFTWのAPIキーは「Read Only」権限で作成すれば十分です。
- 変換対象の実験 (Experiments) に加え、その実験にリンクされたアイテム (Items、`ItemsApi.get_item`)
  も読み取るため、**実験の閲覧権限に加えて、リンク先アイテムの閲覧権限**も必要です
  (通常は同じチーム内であれば問題ありません)。
- 添付ファイルはハッシュ値とダウンロードURLの参照のみ行い、ファイル本体はダウンロードしません。

## APIキーの設定方法

1. eLabFTWにログインし、右上のユーザーメニューから「設定 (Settings)」を開く
2. 「API keys」タブで新規キーを発行 (権限は「Read Only」で問題ありません)
3. 発行されたキーはこの時しか表示されないため、控えておく
4. 環境変数、または`--api-key`引数で本ツールに渡す:
   ```bash
   export ELABFTW_HOST="https://elab.example.org/api/v2"
   export ELABFTW_API_KEY="発行されたAPIキー"
   ```
   `elabftw/desktop` でローカル起動している場合は `--host https://localhost:PORT/api/v2` の
   ように上書きしてください。

## MaiMLへの変換対象

現時点で変換されるのは以下のデータです (詳細は後述のマッピング設計を参照):

| 変換する | 変換しない (対象外) |
| --- | --- |
| 実験のタイトル・作成日時・オーナー | 実験のステータス (Status)・カテゴリ (Category) |
| 実験のStep一覧 (本文・完了日時) | チェックリストの `deadline` (締切日) |
| 実験のExtra Fields (カスタムフィールド) | コメント (Comments) |
| リンクされたアイテム (Items) とそのExtra Fields | 実験間リンク (Links to Experiments)・化合物リンク |
| 実験本文・タグ | Todoリスト、通知、権限設定などのメタ情報 |
| 添付ファイル (ハッシュ値付き参照。ファイル本体は埋め込まない) | |
| 「使用装置」等のカスタムフィールドからのcreator/vendor/instrument | |


## 構成

```
elabftw2maiml/
  uuids.py           UUID生成 (v4乱数 / v5名前ベース)
  model.py           eLabFTW非依存の中間データモデル (ExperimentData 等)
  maiml_xml.py        MaiML要素の低レベル構築 (property/content/globalObjectContentGroup等)
  builder.py          ExperimentData -> <maiml> ルート要素の組み立て
  elabftw_client.py   elabapi-python でeLabFTWから取得 -> ExperimentDataへ変換
elabftw_to_maiml.py    CLIエントリポイント
test_build_and_validate.py  合成データでのビルド+XSD検証テスト（実行には"./schema/"が必要）
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

### コマンドライン引数一覧

| 引数 | 必須 | 既定値 | 説明 |
| --- | --- | --- | --- |
| `--experiment-id` | **必須** | なし | 変換対象のeLabFTW実験ID (数値、1件のみ) |
| `--output` / `-o` | **必須** | なし | 出力する `.maiml` ファイルのパス |
| `--host` | 任意 | 環境変数 `ELABFTW_HOST` | eLabFTW APIのベースURL (例: `https://elab.example.org/api/v2`)。`--host`か環境変数のどちらかが必須 |
| `--api-key` | 任意 | 環境変数 `ELABFTW_API_KEY` | eLabFTWのAPIキー。`--api-key`か環境変数のどちらかが必須 |
| `--insecure` | 任意 (フラグ) | 無効 | TLS証明書検証を無効化 (自己署名証明書のローカル環境向け) |
| `--ns-prefix` | 任意 | `ns1` | `property`/`content` の `key` 属性に使う名前空間プレフィックス |
| `--ns-uri` | 任意 | `https://example.org/maiml/mylab` | `ns-prefix` に対応する名前空間URI (**実運用では必ず自組織のURIを指定**) |
| `--creator-field` | 任意 (複数指定可) | 既定候補 (「使用装置」「Instrument」等) | `creator`(使用装置)として扱うカスタムフィールド名 |
| `--vendor-field` | 任意 (複数指定可) | 既定候補 (「装置メーカー」「Vendor」等) | `vendor`(装置メーカー)として扱うカスタムフィールド名 |

実質必須な組み合わせ:

```bash
python elabftw_to_maiml.py \
    --experiment-id 123 \
    --output out.maiml \
    --host https://elab.example.org/api/v2 \
    --api-key xxxxxxxxxxxx
```

(`--host`/`--api-key`は環境変数で渡せば省略可)

## マッピング設計 (eLabFTW -> MaiML)

| eLabFTW側のデータ | MaiML側 | 備考 |
| --- | --- | --- |
| 実験 (Experiment) 1件 | `document` 1件 + `protocol/method` 1件 + `data` 1件 | `elab_id` を各id属性のsuffixに使用 |
| 実験オーナー (userid, fullname) | `document/owner` | `userid` から名前ベースUUID (v5) を生成。同一ユーザーは常に同一UUID |
| 実験のカスタムフィールド「使用装置」等 (既定候補名で検索) | `document/creator` + `document/instrument` | 値が入力されていれば、その表示名から名前ベースUUIDを生成。見つからなければこのツール自身にフォールバック |
| 実験のカスタムフィールド「装置メーカー」等 (既定候補名で検索) | `document/vendor` | creatorは見つかったがvendorフィールドが無い場合は「メーカー不明」のダミーvendorを作成 (creatorTypeがvendorRefを1つ以上要求するため)。creator自体が見つからなければDeltablot (eLabFTW開発元) にフォールバック |
| 実験の作成日時 (`date`/`created_at`) | `document/date` | ISO8601に変換 |
| 実験のStep一覧 (Steps API, `ordering`順) | `protocol/method/pnml` の `transition` (直列に接続) + `program/instruction` | 1 Step = 1 transition = 1 instruction |
| リンクされたアイテム (`items_links`、詳細は `ItemsApi.get_item`) | 最初のSTEPの `materialTemplate`(M1) + `data/.../material` | アイテムのExtra Fieldsを `property` に変換。実データはここに持たせる |
| 実験のExtra Fields (`metadata.extra_fields`。creator/vendorに使ったフィールドは除外) | 最初のSTEPの `conditionTemplate`(C1) + `data/.../condition` | 1つの `conditionTemplate`/`condition` にまとめて格納 |
| 実験本文 (`body`, HTMLタグ除去) / タグ (`tags`) / 添付ファイル | 最後のSTEPの `resultTemplate`/`result` の `property`/`insertion` | 実データは最後のSTEPに集約する |

### STEP間のmaterial/condition/result連鎖

MaiMLでは各STEPがmaterial/condition/resultを持ち、直列に接続する場合は前STEPの結果が
次STEPの入力材料として引き継がれる、という考え方をとります。eLabFTWのSteps APIには
Step単位の構造化されたmaterial/result情報が無いため (`body`の自由記述テキストのみ)、
以下の既定パターンで機械的に接続しています:

| STEP | 入力 | 出力 (resultTemplate) | 汎用データコンテナ |
| --- | --- | --- | --- |
| 最初のSTEP (R1) | `materialTemplate`(M1) + `conditionTemplate`(C1) | `templateRef`でM1を参照 | なし (実データはM1側に既にある) |
| 途中のSTEP (Ri) | 直前の結果 (R1) | `templateRef`でR1を参照 | なし (参照先から自動継承) |
| 最後のSTEP (Rn) | 直前のSTEPの結果 (R{n-1}) | `templateRef`で直前のresultTemplateを参照 | あり (実験本文/タグ/添付ファイル) |

`data/results` 内の `material`/`condition`/`result` インスタンスも、`instanceRef` で同じ接続パターンを反映します
(`templateRef`のインスタンス層版)。1 STEPのみの実験ではこの連鎖は発生せず、単純に
M1→R1(templateRef)という1段の参照になります。

**制約**: 途中で新しい試料 (別material) を追加する分岐フローは、eLabFTW側にSTEP単位の
構造化情報が無いため自動判定できません。そのような実験がある場合は、生成後のXMLを
手動編集するか、`elabftw_client.py`側でStep本文の記法解析等を追加実装してください。
| 添付ファイル (`uploads`, ハッシュ値含む) | `data/.../result/insertion` | ダウンロードURL + ハッシュ値を参照として記録 (ファイル本体はMaiMLに埋め込まない) |
| 各Stepの開始/終了 (`finished_time`) | `eventLog/log/trace/event` (start/complete) | 最終Stepのcompleteイベントに `resultsRef` を付与 (仕様R-16準拠) |

### creator/vendor/instrumentの既定検索フィールド名

eLabFTWにはMaiMLの `creator`(計測装置・ソフトウェア) / `vendor`(装置メーカー) に対応する専用フィールドが
無いため、実験のカスタムフィールド (Extra Fields) から**大文字小文字を区別せず**以下の候補名を探して転用します。
見つからない場合は creator=このツール自身 / vendor=Deltablot にフォールバックします。

| 用途 | 既定候補フィールド名 |
| --- | --- |
| creator (使用装置) | `使用装置`, `使用機器`, `装置`, `機器`, `Instrument`, `Equipment`, `Device` |
| vendor (装置メーカー) | `装置メーカー`, `メーカー`, `製造元`, `Vendor`, `Manufacturer` |

自組織のフィールド名がこれと異なる場合は、CLIの `--creator-field` / `--vendor-field`
(複数指定可) で上書きできます:

```bash
python elabftw_to_maiml.py --experiment-id 123 --output out.maiml \
    --creator-field "計測機器名" --vendor-field "機器製造元"
```

同一の装置名を持つ実験は常に同じ `creator`/`vendor`/`instrument` UUIDになります。ただし**同一型式の
個体差 (シリアル番号違い) までは区別しません**。個体を厳密に区別したい場合は、フィールド値に
シリアル番号を含めて入力する運用にするか、`elabftw_client.py` の `_creator_vendor_parties` を
拡張してください。

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
- **`instrument`/`creator`/`vendor` はカスタムフィールドの自由記述に依存**します。フィールドが未入力の実験では
  ツール自身/Deltablotのプレースホルダになるため、装置来歴が重要な場合は入力ルールをチーム内で徹底してください。
  また個体識別 (シリアル番号) までは自動区別しないため、同型式の別個体を区別したい場合は運用上の工夫
  (フィールド値にシリアル番号を含める等) が必要です。
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
