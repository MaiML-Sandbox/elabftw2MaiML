# Changelog

All notable changes to this project are documented in this file.

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
