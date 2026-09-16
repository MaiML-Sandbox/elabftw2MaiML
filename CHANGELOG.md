# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

`elabftw2MaiML_phase5_design.md` の Phase 5-1 (汎用接続基盤)・Phase 5-2
(設定可能なフィールドマッピング)・Phase 5-3 (MaiMLへの反映) に対応する。

### Added

- Added `elabftw2maiml/interpretation/pipeline.py`
  (`InterpretationPipeline`, `InterpretationReport`, `partition_candidates()`,
  `format_interpretation_report()`)。実験本文・各Step本文を
  `TextRuleInterpreter` に渡して自由記述候補を収集し (context は実験全体なら
  `"experiment"`、Stepごとなら `"step:<elab_id>"`)、呼び出し側が用意した構造化
  候補と統合したうえで `detect_conflicts()` を実行し、結果を
  `accepted`(自動反映可能)/`conflicts`(競合)/`unclassified`(role・target等が
  未確定)に仕分ける。
- Added `role`/`target` fields to `InterpretationCandidate`
  (`interpretation/conflict.py`)。MaiML上の役割 (`material`/`condition`/
  `result`等) と実際の反映先 (`materials`/`condition_properties`/
  `result_properties`等) を候補に持たせることで、`role`/`target`/
  `semantic_type`/`context`が確定しconfidenceが十分な候補だけを自動反映可能と
  判定できるようにした (既存フィールドはデフォルト`None`のため既存コードへの
  影響なし)。
- Added `tests/test_pipeline.py` (9 tests)。
- Added `elabftw2maiml/interpretation/field_mapping.py`
  (`RawField`, `FieldRule`, `FieldMapping`, `candidate_from_field()`,
  `build_structured_candidates()`)。eLabFTWのCustom Field名
  (またはalias) を `role`/`semantic_type`/単位/`context`/`target`に対応付ける
  設定可能な対応表。対応表自体はコードに固定せず、`FieldMapping.from_dict()`/
  `from_yaml_file()` によりYAML/dictとして外部化しており、研究分野・組織・
  テンプレートが変わっても対応表の差し替えだけで済む。対応表に無いフィールドは
  黙って捨てず`build_structured_candidates()`の戻り値 (`unmapped_fields`) として
  報告する。同名フィールドがSEM/TEM等で意味だけ共通し文脈が異なる場合
  (例:「加速電圧」) は、呼び出し側が`context_override`/`context_for`で
  対応表側の値を上書きできる。
- Added `elabftw2maiml/interpretation/field_mappings/sem_tem_example.yaml`。
  SEM_TEM_field_mapping_example.md 10節のMVPフィールド一覧 (SEM 10件・TEM 11件)
  に対応する、装置メーカーや特定研究室に依存しない汎用的な対応表の例
  (実運用では必ずフィールド名・単位を調整すること)。
- Added `tests/test_field_mapping.py` (27 tests)。
- Added `elabftw2maiml/interpretation/profiles/sem_tem.py`
  (`SemTemTextRuleInterpreter`)。SEM/TEM観察の自由記述から、加速電圧・
  作動距離・倍率・プローブ電流・試料傾斜角・粒径・格子縞間隔・カメラ長の
  8種 (Phase 1) を抽出する。汎用の`TextRuleInterpreter` (`interpretation/
  text.py`) には単位を追加せず、`interpretation/profiles/`配下に完全に分離
  した専用クラスとして実装した。誤検出を防ぐため、次の方針を採用している:
  - 意味キーワード (「加速電圧」「WD」「粒径」等) + 数値 + 単位の組み合わせ
    のみを対象とし、単位・数値のみの表現 ("5 kV"単体等) は意味種別を
    確定できないため一切抽出しない。
  - 倍率は、ローマ字"x"/"X"単体表記 ("50000x"/"x50000"、次元表記
    "10 x 20"等) を対象外とし、「倍率」キーワードを伴う表現、または
    「数値+倍(漢字)+観察/撮影等の文脈語」の組み合わせのみを安全な表現として
    対象にする (漢字の「倍」単体は希釈倍率等の一般的な用法と混同しうるため、
    キーワードまたは観察文脈語を必須とした)。
  - confidenceは既存の`policy.py`の仕組み (`method="regex"`のまま) にそのまま
    乗せており、`ExtractedValue.confidence`は1.0、
    `InterpretationCandidate.confidence`は既存の`free_text_regex`ポリシーに
    従い0.95になる (`policy.py`自体の変更は無し)。
  - 実際のCustom Field名や第2段階の意味種別 (pixel_size/dwell_time/
    chamber_pressure等) は、実際の自由記述を収集してから別途追加する。
- Added `tests/test_sem_tem_profile.py` (33 tests)。安全な倍率表現5種と、
  除外対象の表現 (ローマ字"x"単体・次元表記・キーワード無しの「数値+倍」等)
  の両方を回帰テストとして固定した。
- Added `elabftw2maiml/interpretation/apply.py`
  (`apply_interpretation_report()`)。Phase 5-3: `InterpretationReport`の
  `accepted`候補 (競合が無く、role/semantic_type/context/targetが確定した
  候補) だけをExperimentDataへ反映する。`conflicts`/`unclassified`は
  絶対に反映せず、反映ログ (List[str]) を返す。既存の値は上書きせずスキップ
  する。`target`ごとの反映先は次の4種類:
  `condition_properties`/`result_properties` (そのまま追加)、
  `materials` (`elab_id=0`の合成`LinkedItem`にまとめる。既存の
  `elabftw_client.py`のグループ名ベース振り分けと同じ規約を再利用し、
  合成アイテムが2つに分裂しないようにした)、`instrument`
  (`ExperimentData.instruments`へ`Party`を追加)。
- Added `elabftw2maiml/elabftw_client.py`: `raw_fields_from_metadata()`
  (モジュール関数) / `ElabftwClient.fetch_raw_custom_fields()`
  (Phase 5-3)。実験のExtra Fieldsを`interpretation.field_mapping.RawField`
  のリストに変換する (フィールドのグループ名を`RawField.group`に保持する)。
  既存の`fetch_experiment()`とは独立して呼べる。
- Changed `elabftw2maiml/interpretation/pipeline.py`:
  `InterpretationPipeline`に`extra_text_interpreters`引数を追加
  (後方互換、既定`None`で従来と同じ挙動)。SEM/TEM等の分野固有プロファイル
  (`interpretation/profiles/`) を、汎用の`TextRuleInterpreter`と組み合わせて
  同時に実行できるようにした
  (SEM_TEM_field_mapping_example.md 7節「汎用InterpreterとSEM/TEM固有ルールを
  分離する」構成に対応)。
- Added `elabftw_to_maiml.py`: `--field-mapping PATH` (任意) オプション。
  指定すると、実験のExtra Fields (`--field-mapping`のYAML対応表経由) と
  自由記述 (実験本文・Step本文) を統合し、食い違いの無い値だけを自動反映して
  MaiMLを生成する。反映結果・競合・未分類の候補・対応表に無いフィールドは
  標準出力にレポートとして表示される。`--confidence-threshold`
  (既定`1.0`) も追加。**このオプションを指定しない場合の動作は一切変更して
  いない** (既存の全回帰テストが変更なしで通ることを確認済み)。
- Added `tests/test_apply.py` (14 tests)、
  `tests/test_raw_fields_from_metadata.py` (6 tests)、
  `tests/test_phase5_3_end_to_end.py` (16 tests。構造化フィールドと自由記述が
  一部で食い違う、より実際に近いシナリオの統合テスト。生成MaiMLの整形式性・
  XSD妥当性の検証を含む)、`tests/test_cli_field_mapping.py` (3 tests)。

### Fixed

- Fixed structured field values with embedded units (e.g. `"200 kV"`) being
  misdetected as conflicts against agreeing free-text values. Added
  `elabftw2maiml/interpretation/normalize.py`
  (`NormalizedValue`, `canonical_unit()`, `parse_numeric_with_unit()`)。
  競合判定 (`detect_conflicts()`) の前に、構造化フィールドの値を自由記述側と
  同じ内部表現 (数値 (`Decimal`) + 正規化後の単位) に揃える。
  - 空白の除去 (`"200 kV"`/`"200kV"`)、µ/μ/u・°/度などの表記統一、対応表の
    `unit`による裸数値の単位補完、同一単位での比較までをスコープとし、
    **単位換算 (V→kV等) は初期実装では行わない** (対応表が期待する単位と
    次元が異なる値、または数値として解釈できない値は、正規化できないものと
    して扱う)。
  - 正規化できなかった値は、誤って自動反映されたり誤って競合扱いされたり
    することなく`unclassified`に残るよう、`role`/`target`を`None`にする
    (`field_mapping.py`の`candidate_from_field()`)。元の値
    (`InterpretationCandidate.raw_value`) と正規化できなかった理由
    (`InterpretationCandidate.reason`) は変換実行時のレポート
    (`format_interpretation_report()`/`format_conflict_report()`) に残し、
    黙って捨てない。
  - 正規化に成功した場合も、元の文字列 (例: `"200 kV"`) は`raw_value`として
    保持し、変換結果を後から原記録と照合できるようにした。
  - Added `tests/test_normalize.py` (23 tests)。ユーザーから提示された
    正規化の対応表をそのまま回帰テストとして固定した。
  - Extended `tests/test_field_mapping.py` with 8 tests
    (`TestCandidateFromFieldNormalization`) covering embedded-unit strings,
    no-space strings, bare numeric values/strings with unit fill-in,
    dimension mismatches, and non-numeric values.
  - Extended `tests/test_phase5_3_end_to_end.py` with 4 tests
    (`TestRealisticStringValuesWithEmbeddedUnits`) covering the end-to-end
    behavior (embedded-unit agreement, embedded-unit vs. disagreeing
    free text, dimension mismatch, non-numeric value) including reflection
    into `ExperimentData`.
  - 公式XSD (`maiml-schema-validator`スキル同梱のスキーマ) による検証済み
    (正規化後の`Decimal`値もMUSTレベルのエラーなく`doubleType`として出力
    される)。
- Fixed `field_mapping.py`'s `candidate_from_field()` skipping numeric
  normalization entirely for fields that declare `data_type: number` but no
  expected `unit` (discovered while adjusting the mapping table to real
  Custom Field names below — e.g. `DwellTime`="10", `PixelSize`="0.025", 単位が
  値にも対応表にも存在しない)。従来は`unit`が指定されていなければ正規化その
  ものをスキップし、値が文字列のまま (`stringType`として) 出力されていた。
  `data_type: number`が明示されていれば、`unit=None`のまま
  `parse_numeric_with_unit()`を呼び (単位の食い違いチェックはスキップされる)、
  `Decimal`として正規化する。`unit`も`data_type: number`も無いフィールド
  (文字列フィールドの大半) は従来通り正規化をスキップする。

### Added (Phase 5-4: 実データによる検証、対応表の調整)

- Added `elabftw2maiml/interpretation/field_mappings/yasunaga_lab_stem.yaml`。
  YasunagaLabのSTEM実験で実際にeLabFTW上に存在するMATERIAL/CONDITION/RESULT
  フィールドグループのCustom Field名 (2026-09-16にユーザーから共有) に合わせた
  対応表。`sem_tem_example.yaml`とは異なり、装置メーカー・研究室に依存しない
  汎用例ではなく、実際のフィールド名 (`AcceleratingVoltage(kV)`、
  `Magnification`、`sampleID`等) をそのまま主キーとして使う。
  - `DwellTime`/`PixelSize`は、値の文字列にもフィールド名にも単位が無く、
    実際の単位が未確認のため`unit`を指定していない (ファイル内にコメントで
    明記。要ユーザー確認)。
  - `Grid`の値は共有されたスクリーンショットの文字列がドロップダウンの
    選択肢一覧のように見え、実際に選択された1件の値かどうか未確認
    (要ユーザー確認)。
- Added `tests/test_yasunaga_lab_stem_field_mapping.py` (11 tests)。
  ユーザーから共有された実際の値 (単位混在文字列を含む) をそのまま
  `RawField`として与え、MATERIAL/CONDITION/RESULTの全20フィールドが対応表に
  マッチすること、構造化フィールドのみでは誤って競合が検出されないこと、
  自由記述と一致/不一致の両ケースが正しく扱われること、生成MaiMLが整形式
  であることを確認する。公式XSDによる検証も別途実施し、MUSTレベルのエラーが
  無いことを確認済み。

### Notes

- **Phase 5-3により、`--field-mapping`を指定した場合に限り、構造化フィールド・
  自由記述からの候補がMaiML出力に実際に反映されるようになった。** ただし
  `--field-mapping`を指定しない (これまでどおりの) 使い方には一切影響しない
  (既存の全回帰テストが変更なしで通ることを確認済み)。合成データによる
  統合テスト (`tests/test_phase5_3_end_to_end.py`) と、実際の公式XSD
  (`maiml-schema-validator`スキル同梱のスキーマ) による検証は行ったが、
  **実際のeLabFTWサーバーに接続した確認 (実際のREST API経由での取得) は
  まだ行っていない**。上記「Phase 5-4」節の通り、実際のCustom Field名・値
  (スクリーンショット共有分) を使った対応表調整と合成データでの統合テストは
  実施済み。特に次の点は依然として要確認:
  - `DwellTime`/`PixelSize`の実際の単位 (`yasunaga_lab_stem.yaml`では未設定)。
  - `Grid`フィールドの値がドロップダウンの選択肢一覧なのか実際の選択値なのか。
  - eLabFTWのExtra Fieldsに値と単位が混在する問題 (例: `"200 kV"`) は
    上記「### Fixed」の単位正規化により対応済みだが、**単位換算
    (V→kV等) は初期実装のスコープ外**のため、対応表が期待する単位と
    実際の入力形式 (例: `"200000 V"`) が異なる場合は依然として自動反映
    されない (`unclassified`に残る)。実データで、対応表の`unit`と実際の
    入力形式が一致しているか確認する必要がある。
- SEM/TEM第2段階の意味種別 (pixel_size/dwell_time/chamber_pressure/defocus/
  electron_dose/objective_aperture/selected_area_aperture/膜厚・薄片厚さ・
  空孔径等) は、実際の自由記述の表記ゆれを収集してから追加する。

## [0.2.0] - 2026-09-16

`elabftw2MaiML_phase1_development_plan.md` の Phase 1〜4 に対応するリリースです。
既存の変換結果 (role判定・MaiML出力) を一切変えずに内部ロジックを整理したうえで、
自由記述からのルールベース抽出と、構造化フィールドとの競合検出という新しい基盤を
追加しました。

### Added

- Separated structured classification logic from the eLabFTW API client
  (`elabftw2maiml/interpretation/structured.py`; behavior-preserving refactor,
  covered by golden-snapshot regression tests).
- Added rule-based extraction from free-text descriptions
  (`elabftw2maiml/interpretation/text.py`: `TextRuleInterpreter`).
- Added extraction support for temperature, duration, mass, volume, rotation
  speed, and pH.
- Added provenance information to interpretation results (source text,
  extraction method, confidence) via `InterpretationResult` / `ExtractedValue`.
- Added confidence handling for structured and free-text sources
  (`elabftw2maiml/interpretation/policy.py`: `DEFAULT_SOURCE_CONFIDENCE`,
  `candidate_from_extracted_value()`). Structured sources (`custom_field` 等)
  は confidence=1.0、自由記述からの正規表現抽出 (`free_text_regex`) は暫定的に
  confidence=0.95 とし、抽出自体の確実性 (`ExtractedValue.confidence`) とは
  別レイヤーとして扱う。
- Added conflict detection between structured fields and free-text values
  (`elabftw2maiml/interpretation/conflict.py`: `InterpretationCandidate`,
  `Conflict`, `detect_conflicts()`, `format_conflict_report()`)。一致しない値は
  一方で他方を上書きせず、両方を候補として保持したうえで競合として報告する。
- Added regression and text-extraction tests (`tests/`: golden-snapshot
  regression, `TextRuleInterpreter` 単体テスト, 競合検出の単体テスト,
  confidenceポリシーの単体テスト)。特にリリース基準として次の3ケースを固定した
  (`tests/test_interpretation_scenarios.py`): (1) 構造化フィールドのみ、
  (2) 自由記述のみ、(3) 構造化フィールドと自由記述が矛盾する場合。

### Fixed

- `builder.py`: 最初のSTEPの `resultTemplate`/`result` が `templateRef`/
  `instanceRef` で異なる種類の要素 (`materialTemplate`/`material`) を参照して
  おり、MaiML標準の「同種の要素のみ参照可」という制約 (共通指示書4.2/5.1) に
  違反していた。実際のeLabFTWデータで公式XSD検証を行って判明。material→result
  の関係は既にpnmlのarc・共有placeRefで表現されているため、最初のSTEPでは
  これらの参照を単純に省略するよう修正 (どちらも仕様上 `minOccurs="0"` で
  省略可能)。golden fixtureはこの修正を反映して再生成済み。

### Changed

- 内部の `CREATOR_SOFTWARE_VERSION` (MaiML内の「変換ソフトウェア」エンティティの
  UUID生成に使う識別子) をプロジェクトのリリース番号 `0.2.0` に合わせて更新。

### Notes

Free-text interpretation in this release is deterministic and rule-based
(regular expressions + unit normalization only). LLM-based semantic
interpretation is not included, and the free-text/conflict-detection
utilities are not yet wired into `fetch_experiment()` / `ExperimentData` /
MaiML output — they are standalone, unit-tested components pending real
eLabFTW custom-field naming conventions before being connected
(development plan Phase 5, deferred).

## [0.1.0]

最初の公開バージョン。eLabFTW REST API v2 経由で取得した実験データを、
Category/Tag/Custom Field Groupに基づく役割判定 (material/condition/result/
creator/vendor/instrument) を通じて MaiML (JIS K 0200) 形式に変換する。
