# elabftw2maiml

**バージョン: v0.2.0** (変更履歴は [CHANGELOG.md](./CHANGELOG.md) を参照)

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
  interpretation/      構造情報・自由記述からの判定ロジック (elabftw_client.pyから分離)
    model.py            判定結果モデル (InterpretationResult: role/confidence/source/reason)
    structured.py        StructuredRuleInterpreter (Category/Tag/Custom Field GroupからのRole判定)
    text.py              TextRuleInterpreter (自由記述からの温度/時間/質量/体積/回転数/pH抽出)
    conflict.py          InterpretationCandidate/Conflict (構造化情報と自由記述の値の突き合わせ)
    policy.py             情報源ごとのconfidenceポリシー (DEFAULT_SOURCE_CONFIDENCE等)
    pipeline.py            InterpretationPipeline/InterpretationReport (Phase5-1: 自由記述候補の
                          収集・構造化候補との統合・競合検出・仕分けを行う接続基盤。
                          extra_text_interpreters引数で、SEM/TEM等の分野固有プロファイル
                          (profiles/以下) を汎用のTextRuleInterpreterと組み合わせられる)
    apply.py               apply_interpretation_report() (Phase5-3: InterpretationReportの
                          accepted候補だけをExperimentDataへ反映する。競合(conflicts)・
                          未分類(unclassified)は反映せず、反映ログ(List[str])を返す。
                          既存の値は上書きせずスキップする)
    field_mapping.py        RawField/FieldRule/FieldMapping (Phase5-2: eLabFTWのCustom Field名を
                          role/semantic_type/unit/context/targetに対応付ける設定可能な対応表。
                          対応表自体はYAML/dictとして外部化されており、コードには固定しない)
    field_mappings/
      sem_tem_example.yaml  SEM/TEM観察の汎用的な対応表の例 (SEM_TEM_field_mapping_example.md
                          10節のMVPフィールド一覧に対応。装置メーカーや特定研究室に依存しない
                          一般例であり、実運用ではフィールド名・単位を必ず調整すること)
    profiles/
      sem_tem.py            SemTemTextRuleInterpreter (SEM/TEM固有の自由記述からの
                          加速電圧・作動距離・倍率・プローブ電流・試料傾斜角・粒径・
                          格子縞間隔・カメラ長の抽出。汎用のtext.pyには単位を追加せず、
                          意味キーワード+数値+単位の組み合わせのみを対象とすることで
                          誤検出を防ぐ。倍率のローマ字"x"単体表記は対象外)
elabftw_to_maiml.py    CLIエントリポイント
test_build_and_validate.py  合成データでのビルド+XSD検証テスト（実行には"./schema/"が必要）
tests/                 pytestによる回帰テスト・単体テスト (詳細は下記「テスト」参照)
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
| `--role-category` | 任意 (`ROLE=値` 形式、複数指定可) | 既定候補 (役割ごと。下記参照) | リンクされたアイテムをROLE (material/condition/result/creator/instrument/vendor) として扱うカテゴリ名 |
| `--role-tag` | 任意 (`ROLE=値` 形式、複数指定可) | 既定候補 (役割ごと。下記参照) | リンクされたアイテムをROLEとして扱うタグ名 |
| `--field-group` | 任意 (`ROLE=値` 形式、`material`/`result`のみ、複数指定可) | 既定候補 (「MATERIAL」「RESULT」等) | 実験自身のカスタムフィールドグループ名からROLEへ振り分け |
| `--instrument-category` | 任意 (複数指定可、非推奨) | 既定候補 (「Resources」「装置」等) | `--role-category instrument=...`と同じ (互換用) |
| `--instrument-tag` | 任意 (複数指定可、非推奨) | 既定候補 (「Resources」「装置」等) | `--role-tag instrument=...`と同じ (互換用) |
| `--field-mapping` | 任意 | 無し (指定しない限りPhase5の処理は一切実行されない) | Phase5-2/5-3: 実際のCustom Field名をsemantic_type/role/unit/context/targetに対応付けるYAML設定ファイルのパス。詳細は次節「対応表による構造化フィールド・自由記述の統合 (Phase 5-2/5-3)」参照 |
| `--confidence-threshold` | 任意 (`--field-mapping`指定時のみ有効) | `1.0` | 候補を自動反映するconfidenceの閾値 |

実質必須な組み合わせ:

```bash
python elabftw_to_maiml.py \
    --experiment-id 123 \
    --output out.maiml \
    --host https://elab.example.org/api/v2 \
    --api-key xxxxxxxxxxxx
```

(`--host`/`--api-key`は環境変数で渡せば省略可)

## 対応表による構造化フィールド・自由記述の統合 (Phase 5-2/5-3)

通常の変換 (`--field-mapping` を指定しない場合) は、実験のExtra Fieldsを
eLabFTWの「フィールドグループ」機能 (MATERIAL/CONDITION/RESULT) だけで
material/condition/resultに振り分けます (Phase 1〜4の既存動作、変更なし)。

`--field-mapping` にYAML設定ファイル (書式は
`elabftw2maiml/interpretation/field_mappings/sem_tem_example.yaml` を参照。
**このファイル自体は装置メーカー・特定研究室に依存しない汎用例であり、実運用では
必ず自組織の実際のCustom Field名に合わせて調整すること**) を渡すと、追加で
次の処理を行います:

1. 対応表に定義されたCustom Field名を `semantic_type`/`role`/`context`/
   `target` を持つ候補 (structured candidates) に変換する。
2. 実験本文・各Stepの本文から、自由記述のルールベース抽出
   (`TextRuleInterpreter` + SEM/TEM固有の`SemTemTextRuleInterpreter`) で
   候補を収集する。
3. 1と2を統合し、同じ意味種別・同じcontextの値が食い違っていないか確認する
   (`interpretation.detect_conflicts()`)。
4. 食い違いが無く、`role`/`semantic_type`/`context`/`target`が全て確定した
   候補だけを自動的にExperimentDataへ反映する (`role`/`target`が対応表に
   無い、または食い違いがある値は**自動反映しない**)。
5. 反映結果・競合・未分類の候補は、変換実行時に標準出力へレポートとして
   表示される (対応表に定義の無いフィールド名も、黙って無視せず一覧表示する)。

```bash
python elabftw_to_maiml.py --experiment-id 123 --output out.maiml \
    --field-mapping elabftw2maiml/interpretation/field_mappings/sem_tem_example.yaml
```

**この機能で実際にExperimentDataへ反映できる`target`は次の4種類のみ**
(それ以外の`target`値を対応表に書いても、反映されずログに警告が出るだけ):

| `target` | 反映先 | 備考 |
| --- | --- | --- |
| `condition_properties` | `ExperimentData.condition_properties` | そのまま追加 |
| `result_properties` | `ExperimentData.result_properties` | そのまま追加 |
| `materials` | `ExperimentData.materials` | `elab_id=0`の合成`LinkedItem`にまとめる (既存の「MATERIALグループの自己カスタムフィールド」と同じ規約を再利用し、二重に分裂させない) |
| `instrument` | `ExperimentData.instruments` | 値を表示名とする`Party`を追加 (同名は重複追加しない) |

既に同じキー (semantic_typeから生成) の値が存在する場合は、**上書きせずスキップ**
します (development planの「一方で他方を上書きしない」という既存方針を、
ExperimentDataへの反映段階でも維持しています)。

**構造化フィールドの単位正規化**: eLabFTWのExtra Fieldsは値と単位を別々に持つ
仕組みが無く、単位はフィールド名 (`AcceleratingVoltage(kV)`等) や値の文字列
(`"200 kV"`等) に含まれることがあります。競合判定 (`detect_conflicts()`) の前に、
`interpretation/normalize.py` の`parse_numeric_with_unit()`が構造化フィールドの値を
自由記述側と同じ内部表現 (数値 + 正規化後の単位、内部的には`Decimal`) に揃えます。

- 空白の有無 (`"200 kV"`/`"200kV"`) や、µ/μ/u・°/度などの表記の揺れは同一の単位
  として扱う。
- 値が単位を含まない裸の数値 (`"200"`や数値そのもの) の場合は、対応表の`unit`を
  補って正規化する。
- **単位換算 (V→kV等) は行わない** (初期実装のスコープ外)。対応表が期待する単位と
  次元が異なる値 (例: 期待単位`kV`に対し`"200 mA"`) や、数値として解釈できない値
  (例: `"not measured"`) は、正規化できないものとして扱い、`role`/`target`を
  未確定にする。これにより、その値は競合としても正常値としても扱われず
  `unclassified`に残り、変換実行時のレポートに元の値 (`raw_value`) と理由
  (`reason`) が表示される (自動反映もされないが、黙って捨てられることもない)。
- 正規化に成功した値は`InterpretationCandidate.raw_value`に元の文字列
  (例: `"200 kV"`) を保持したままなので、変換結果を後から原記録と照合できる。

単位換算が必要な値 (例: `"200000 V"`を`200 kV`として扱いたい場合) は、現時点では
対応表の`unit`と実際の入力形式を揃えるか、値の前処理を別途検討してください
(development planのPhase 5-4「実データによる検証」で確認する想定の項目です)。

## テスト

実際のeLabFTWサーバーに接続せず、合成 (synthetic) フィクスチャに対して
`elabftw_client.py`/`builder.py` の変換ロジックを検証する回帰テスト一式を
`tests/` に用意している。

```bash
pip install pytest
python -m pytest tests/
```

- `tests/fixtures.py`: `elabapi_python` のSDKモデルを実際に呼び出さず、
  `ElabftwClient` が参照する属性だけを持つ合成データ (Category/Tag/Custom Field
  Groupによる役割判定、ネストしたリンクアイテム、Steps、添付ファイルなどを網羅) を用意する。
- `tests/test_regression_experiment_data.py`: `fetch_experiment()` が返す
  `ExperimentData` が `tests/golden/experiment_data_*.json` と一致することを確認する
  (Group/Tag/Categoryによる役割判定、Custom Fieldの型変換などが変化していないことの保証)。
- `tests/test_regression_maiml_build.py`: `ExperimentData` から生成される MaiML
  (`.maiml`) が `tests/golden/*.maiml` と一致すること (UUID採番は決定論的な値に
  差し替えて比較)、生成XMLが整形式であること、`schemas/maiml.xsd` が存在する場合は
  それに対して妥当であることを確認する (XSDが無い環境では該当テストをスキップする)。
- `tests/generate_golden.py`: 判定ロジックやMaiML組み立てロジックを**意図的に**
  変更し、新しい出力を今後の回帰テストの基準として採用したい場合にのみ、変更内容を
  レビューした上で `python -m tests.generate_golden` として実行する
  (通常のテスト実行では使わない)。
- `tests/test_text_rule_interpreter.py`: `TextRuleInterpreter` (自由記述からの
  温度・時間・質量・体積・回転数・pH抽出) の単体テスト。development planの
  worked example (「40 ℃で30分加熱した。」) を含む。
  **注意**: `TextRuleInterpreter` は現時点では単体で完結しており、
  `fetch_experiment()`/`ExperimentData`/MaiML出力にはまだ接続していない
  (development planのPhase 4「競合検出」・Phase 5「MaiML出力との接続」で
  今後つなぎ込む予定)。
- `tests/test_conflict.py`: `InterpretationCandidate`/`Conflict`/
  `detect_conflicts()`/`format_conflict_report()` の単体テスト。development
  planの例 (カスタムフィールド: Temperature=50℃ / 自由記述: 40℃で30分加熱した。
  が競合として検出されること) を含む。
  **注意**: こちらも現時点では汎用の突き合わせロジックのみを提供する単体の
  コンポーネントであり、実際のeLabFTWカスタムフィールドをどの意味種別
  (temperature/duration/...) に対応付けるかの判断はまだ行っていない
  (フィールド名からの自動対応付けは、実運用でのフィールド命名を確認した上で
  別途検討する)。
- `tests/test_policy.py`: `policy.py` (`DEFAULT_SOURCE_CONFIDENCE`/
  `candidate_from_extracted_value()`/`candidate_confidence_for_source()`) の
  単体テスト。`ExtractedValue.confidence` (抽出ルールの一致確実性。正規表現が
  一致すれば常に1.0のまま変更しない) と `InterpretationCandidate.confidence`
  (MaiMLへの採用候補としての確度。情報源ごとのポリシーから決める。
  `custom_field`等は1.0、`free_text_regex`は暫定的に0.95) が別レイヤーである
  ことの確認、および両者の積として候補のconfidenceが計算されることを検証する。
- `tests/test_interpretation_scenarios.py`: リリース時の基準ケースとして固定する
  3パターン ((1) 構造化フィールドのみ、(2) 自由記述のみ、(3) 構造化フィールドと
  自由記述が矛盾する場合) を、`TextRuleInterpreter.extract()` ->
  `candidate_from_extracted_value()` -> `detect_conflicts()` という実際の
  呼び出し順序で通し、組み合わせたときの挙動を今後の回帰基準として固定する。
- `tests/test_pipeline.py`: `interpretation/pipeline.py` (Phase5-1の接続基盤)
  の単体テスト。`ExperimentData`/`Step`の`body_text`/`body`から自由記述候補を
  収集してcontext ("experiment"/"step:<id>") を付与すること、構造化候補
  (呼び出し側が用意したもの) との統合・競合検出、`role`/`target`/`context`/
  `semantic_type`が確定しconfidenceが閾値以上の候補だけが`accepted`になり、
  競合した候補は`accepted`/`unclassified`のどちらにも入らず`conflicts`にのみ
  残ることを検証する。elabftw2MaiML_phase5_design.md 15節の統合テスト方針の
  うち、実フィールド名の対応表 (Phase5-2) を必要としない項目に対応する。
- `tests/test_field_mapping.py`: `interpretation/field_mapping.py` (Phase5-2の
  設定可能なフィールドマッピング) の単体テスト。`FieldMapping.from_dict()`/
  `from_yaml_file()` による対応表読み込み (`field_mappings/sem_tem_example.yaml`
  を実際に読み込むケースを含む)、フィールド名・aliasの両方での`lookup()`、
  `candidate_from_field()`が`role`/`semantic_type`/`unit`/`context`/`target`を
  正しく組み立てること、`context_override`/`role_override`が対応表側の値より
  優先されること (SEM_TEM_field_mapping_example.md 7節: 「加速電圧」等の
  SEM/TEM共通フィールドをCustom Field GroupやStep種別から区別するケース)、
  および`build_structured_candidates()`が対応表に無いフィールドを取り零さず
  `unmapped_fields`として返すこと (design doc 15節-9) を検証する。
- `tests/test_sem_tem_profile.py`: `interpretation/profiles/sem_tem.py`
  (`SemTemTextRuleInterpreter`) の単体テスト。Phase 1で対応する8種の意味種別
  (加速電圧・作動距離・倍率・プローブ電流・試料傾斜角・粒径・格子縞間隔・
  カメラ長) それぞれについて、意味キーワード+数値+単位の組み合わせから正しく
  抽出できることを確認する。特に重点を置いているのは次の2点:
  (1) 意味キーワードを伴わない単位・数値のみの表現 ("5 kV"単体等) は
  意味種別を確定できないため一切抽出されないこと、
  (2) 倍率について、安全な5表現 (「倍率 50,000」等) のみを対象とし、
  ローマ字"x"単体表記 ("50000x"/"x50000") や次元表記 ("10 x 20")、
  キーワード・観察文脈語を伴わない「数値+倍」(希釈倍率等との混同を避ける)
  は対象外とすること。
- `tests/test_raw_fields_from_metadata.py`: `elabftw_client.py`の
  `raw_fields_from_metadata()`/`ElabftwClient.fetch_raw_custom_fields()`
  (Phase5-3: 実際のeLabFTWのExtra Fieldsを`RawField`へ変換する) の単体テスト。
  カスタムフィールドのグループ名 (`extra_fields_groups`) が`RawField.group`に
  正しく渡ること、グループ無しのフィールドは`group=None`になることを検証する。
- `tests/test_apply.py`: `interpretation/apply.py`の
  `apply_interpretation_report()` (Phase5-3: `InterpretationReport`の
  `accepted`候補をExperimentDataへ反映する) の単体テスト。`target`ごとの
  反映先 (condition_properties/result_properties/materials/instrument) が
  正しいこと、既存の値を上書きせずスキップすること、`conflicts`/
  `unclassified`は絶対に反映されないこと、`materials`への反映が既存の
  `elabftw_client.py`の`elab_id=0`合成アイテムの規約と衝突せず1つに
  まとまることを検証する。
- `tests/test_phase5_3_end_to_end.py`: 構造化フィールド (Custom Field) と
  自由記述 (実験本文) が一部で食い違うという、より実際に近いシナリオを使った
  統合テスト。「値が食い違う項目は自動反映されない」「値が一致する項目は
  構造化側のみ1回だけ反映される」「対応表に無い言及は自由記述に無ければ
  そのまま反映される」ことを、`build_structured_candidates()` ->
  `InterpretationPipeline.interpret_experiment()` ->
  `apply_interpretation_report()` -> `MaimlBuilder.to_bytes()` という実際の
  呼び出し順序で通して確認する。生成XMLの整形式性と、`schemas/maiml.xsd`が
  存在する場合はそれに対する妥当性も検証する (無い環境ではスキップ)。
- `tests/test_cli_field_mapping.py`: `elabftw_to_maiml.py`の`--field-mapping`
  オプション (Phase5-3のCLI統合) のテスト。ネットワークに接続せず
  `tests.fixtures`の合成データで`ElabftwClient`を差し替え、CLI全体が
  例外なく実行できること、対応表に無いフィールドが標準出力に報告されること、
  `--field-mapping`を指定しない場合は関連する出力が一切出ないこと
  (既存動作に影響が無いこと) を確認する。

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

### リンクされたアイテムの役割判定 (material/condition/result/creator/instrument/vendor)

eLabFTWでは試料も装置もカスタムデータも、同じ「Items」データベースでカテゴリ違いとして
管理されているため、実験にリンクされたアイテムを何も考えずに全て`materialTemplate`/`material`に
変換すると、リンクした顕微鏡・分析装置なども「試料」として出力されてしまいます。

これを避けるため、リンクされたアイテムの**カテゴリ名**または**タグ**が下記の候補文字列に
(大文字小文字を区別せず部分一致で) 該当する場合、そのアイテムを対応する役割として扱います。
どれにも該当しなければ既定で `material` として扱われます (従来の挙動と互換)。

| 役割 | 既定候補文字列 (カテゴリ名・タグ共通) | 反映先 |
| --- | --- | --- |
| `creator` | `Creator`, `作成者`, `使用装置`, `使用機器` | `document/creator` (アイテムのタイトルを使用) |
| `vendor` | `Vendor`, `メーカー`, `製造元`, `Manufacturer` | `document/vendor` (アイテムのタイトルを使用) |
| `condition` | `Conditions`, `Condition`, `条件` | `conditionTemplate`/`condition` (アイテムのカスタムフィールドをマージ) |
| `result` | `Results`, `Result`, `結果` | 最後のSTEPの`resultTemplate`/`result` (アイテム名+カスタムフィールドを追加) |
| `instrument` | `Resources`, `Resource`, `Equipment`, `装置`, `機器`, `Instrument` | `document/instrument`、creator/vendor未指定時のフォールバック |
| `material` (既定/フォールバック) | `Consumables`, `Samples`, `Sample`, `試料`, `材料`, `Material` | `materialTemplate`/`material` |

判定の優先順位は上記表の上から順（`creator`→`vendor`→`condition`→`result`→`instrument`→`material`）で、
最初に一致した役割が採用されます。

自組織のカテゴリ名/タグがこれと異なる場合は、CLIの `--role-category` / `--role-tag`
(`ROLE=値` の形式、複数指定可) で役割ごとに上書きできます。指定したROLEのみ既定候補が
置き換わり、他のROLEは既定候補のまま残ります:

```bash
python elabftw_to_maiml.py --experiment-id 123 --output out.maiml \
    --role-category condition="測定条件" \
    --role-category result="分析結果" \
    --role-tag instrument="装置"
```

（`--instrument-category`/`--instrument-tag`は互換性のために残していますが、`--role-category
instrument=...`と同じ意味なので、新規には`--role-category`/`--role-tag`の使用を推奨します）

**creator/vendor/instrumentの決定優先順位**:
1. カテゴリ/タグから明示的に`creator`/`vendor`と判定されたリンクアイテム
2. 実験のカスタムフィールド (`--creator-field`/`--vendor-field`)
3. カテゴリ/タグから`instrument`と判定されたリンクアイテムをcreatorのフォールバックに使う
   (そのアイテム自身のカスタムフィールドから`--vendor-field`候補名でvendorも探す)
4. どれも無い場合、このツール自身 / Deltablot にフォールバック

**condition/resultの決定**: 実験のExtra Fields (condition) / 実験本文・タグ (result) に加え、
カテゴリ/タグから`condition`/`result`と判定されたリンクアイテムのカスタムフィールドも、
それぞれconditionTemplate/resultTemplateにマージされます。

### 実験自身のカスタムフィールドのグループ化対応 (CUSTOM FIELDS内のMATERIAL/CONDITION/RESULT)

eLabFTWのCUSTOM FIELDSは、フィールドをMATERIAL/CONDITIONのような**折りたたみグループ**にまとめる
機能があります。この場合、フィールドのグループ名 (`metadata.elabftw.extra_fields_groups`) から
material/condition/resultのどこに振り分けるかを自動判定します
(前節の「リンクされたアイテムの役割判定」とは別の、実験自身のカスタムフィールド用の仕組みです)。

| 既定候補文字列 (グループ名) | 振り分け先 |
| --- | --- |
| `MATERIAL`, `材料`, `試料`, `Sample` | 試料を表す合成アイテムとして`materialTemplate`/`material`に追加 |
| `RESULT`, `RESULTS`, `結果` | 最後のSTEPの`resultTemplate`/`result`に追加 |
| (上記に一致しないグループ、またはグループ無し) | `conditionTemplate`/`condition` (既定/フォールバック) |

自組織のグループ名がこれと異なる場合は、CLIの`--field-group` (`ROLE=値`の形式、`material`/`result`
のみ指定可、複数指定可) で上書きできます:

```bash
python elabftw_to_maiml.py --experiment-id 123 --output out.maiml \
    --field-group material="試料情報" --field-group result="解析結果"
```

### カスタムフィールドの型マッピング

| eLabFTWの `extra_fields[].type` | MaiMLの `xsi:type` |
| --- | --- |
| `number` | `doubleType` |
| `date` / `datetime-local` | `dateTimeType` |
| `checkbox` | `booleanType` |
| `url` | `uriType` |
| それ以外 (`text`, `select`, `radio`, `items`, `users` 等) | `stringType` |

## 既知の制約・今後の拡張ポイント

- **`metadata`フィールドの型ゆれに対応済み**: eLabFTWのAPIは実験・アイテムの`metadata`を
  JSON文字列のまま返しますが、`elabapi_python`の自動デシリアライズ処理はこの値が
  dict/listでない場合に内容を破棄し、空のオブジェクトを作ります。
  そのため通常の`get_experiment()`/`get_item()`経由では、カスタムフィールド (Extra Fields) が
  常に空になってしまいます。この問題を回避するため、`elabftw_client.py`の`_get_raw_json()`で
  `_preload_content=False`を指定し、SDKのデシリアライズを経由しない生JSONレスポンスから
  直接`metadata`を読み取るようにしています。取得できたフィールド数が0件の場合はコンソールに
  `[情報]`/`[警告]`ログを出すので、実行時に確認してください。
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
  後段で適用してください。
- **ダウンロードURLの形式** (`app/download.php?f=...&name=...`) はeLabFTWのバージョンにより異なる
  可能性があります。実環境に合わせて `elabftw_client.py` の `_fetch_uploads` を調整してください。
- 生成されたXMLはUUID (v4) がビルドの都度変わる第1層要素と、名前ベースUUID (v5) で固定される
  creator/owner/vendorが混在します。同一実験を複数回変換すると、`document`等のUUIDは毎回変わりますが、
  `owner`/`creator`/`vendor`のUUIDは常に同じ値になります。
