"""`elabftw2maiml/interpretation/profiles/sem_tem.py` (SEM/TEM固有の自由記述
抽出ルール) の単体テスト。

ユーザーとの合意事項に基づき、次の3点を重点的に確認する:

1. Phase 1で対応する8種の意味種別が、意味キーワード + 数値 + 単位の組み合わせ
   から正しく抽出できること。
2. 意味キーワードを伴わない単位・数値のみの表現 ("5 kV"単体等) は、
   意味種別を確定できないため一切抽出されないこと (unclassifiedにすら
   値として現れない、という保守的な実装であることの確認)。
3. 倍率 (magnification) について、安全な表現のみを対象とし、ローマ字"x"単体
   表現・希釈倍率等と混同しうる表現は対象外とすること。

confidenceについては、`ExtractedValue.confidence`が常に1.0のままであること
(抽出そのものの確実性)、および`candidate_from_extracted_value()`を通すと
既存の`free_text_regex`ポリシーがそのまま適用されて`InterpretationCandidate.
confidence`が0.95になること (policy.pyの変更なしに、既存の仕組みへ乗せている
ことの確認) を検証する。
"""
from elabftw2maiml.interpretation.profiles.sem_tem import (
    SemTemTextRuleInterpreter,
    SEM_TEM_PHASE1_SEMANTIC_TYPES,
    normalize_sem_tem_unit,
)
from elabftw2maiml.interpretation.policy import candidate_from_extracted_value


class TestPhase1SemanticTypesExtraction:
    """8種それぞれについて、キーワード+数値+単位から正しく抽出できることを
    確認する (design doc記載の代表例を使用)。"""

    def setup_method(self):
        self.interp = SemTemTextRuleInterpreter()

    def test_accelerating_voltage_keyword(self):
        results = self.interp.extract("加速電圧 5 kVで観察した。")
        assert len(results) == 1
        r = results[0]
        assert r.semantic_type == "accelerating_voltage"
        assert r.value == 5
        assert r.unit == "kV"
        assert r.confidence == 1.0
        assert r.method == "regex"

    def test_accelerating_voltage_hv_alias(self):
        results = self.interp.extract("HV=15 kVに設定。")
        assert len(results) == 1
        assert results[0].semantic_type == "accelerating_voltage"
        assert results[0].value == 15
        assert results[0].unit == "kV"

    def test_working_distance_wd(self):
        results = self.interp.extract("WD 8 mmで撮影。")
        assert len(results) == 1
        assert results[0].semantic_type == "working_distance"
        assert results[0].value == 8
        assert results[0].unit == "mm"

    def test_working_distance_japanese(self):
        results = self.interp.extract("作動距離 10 mmとした。")
        assert len(results) == 1
        assert results[0].semantic_type == "working_distance"
        assert results[0].value == 10

    def test_probe_current(self):
        results = self.interp.extract("プローブ電流 100 pAで測定した。")
        assert len(results) == 1
        assert results[0].semantic_type == "probe_current"
        assert results[0].value == 100
        assert results[0].unit == "pA"

    def test_specimen_tilt_kanji(self):
        results = self.interp.extract("試料傾斜角30°で観察した。")
        assert len(results) == 1
        assert results[0].semantic_type == "specimen_tilt"
        assert results[0].value == 30
        assert results[0].unit == "deg"

    def test_specimen_tilt_tilt_keyword(self):
        results = self.interp.extract("tilt=30 degに設定して撮影した。")
        assert len(results) == 1
        assert results[0].semantic_type == "specimen_tilt"
        assert results[0].value == 30
        assert results[0].unit == "deg"

    def test_lattice_spacing(self):
        results = self.interp.extract("格子縞間隔0.204 nmを確認した。")
        assert len(results) == 1
        assert results[0].semantic_type == "lattice_spacing"
        assert results[0].value == 0.204
        assert results[0].unit == "nm"

    def test_particle_size(self):
        results = self.interp.extract("粒径120 nmの粒子が観察された。")
        assert len(results) == 1
        assert results[0].semantic_type == "particle_size"
        assert results[0].value == 120
        assert results[0].unit == "nm"

    def test_camera_length(self):
        results = self.interp.extract("カメラ長500 mmで回折像を撮影した。")
        assert len(results) == 1
        assert results[0].semantic_type == "camera_length"
        assert results[0].value == 500
        assert results[0].unit == "mm"

    def test_all_phase1_types_covered_by_some_test(self):
        # このテストクラス内で、SEM_TEM_PHASE1_SEMANTIC_TYPESの全種別を
        # 最低1つはカバーしていることの自己点検。
        covered = {
            "accelerating_voltage", "working_distance", "probe_current",
            "specimen_tilt", "lattice_spacing", "particle_size",
            "camera_length", "magnification",
        }
        assert covered == set(SEM_TEM_PHASE1_SEMANTIC_TYPES)


class TestMagnificationSafePatterns:
    """design doc記載の「安全性の高い」5表現を全て正しく抽出できることを
    確認する。"""

    def setup_method(self):
        self.interp = SemTemTextRuleInterpreter()

    def test_keyword_plain_number(self):
        results = self.interp.extract("倍率 50,000で観察した試料。")
        assert len(results) == 1
        assert results[0].semantic_type == "magnification"
        assert results[0].value == 50000
        assert results[0].unit == "x"

    def test_keyword_with_bai_suffix(self):
        results = self.interp.extract("倍率50,000倍の画像を取得した。")
        assert len(results) == 1
        assert results[0].semantic_type == "magnification"
        assert results[0].value == 50000
        assert results[0].unit == "x"

    def test_number_bai_with_observation_context(self):
        results = self.interp.extract("50,000倍で観察した。")
        assert len(results) == 1
        assert results[0].semantic_type == "magnification"
        assert results[0].value == 50000
        assert results[0].unit == "x"

    def test_keyword_with_multiplication_sign(self):
        results = self.interp.extract("倍率 50,000×で撮影。")
        assert len(results) == 1
        assert results[0].semantic_type == "magnification"
        assert results[0].value == 50000
        assert results[0].unit == "x"

    def test_english_magnification_keyword(self):
        results = self.interp.extract("magnification: 50000")
        assert len(results) == 1
        assert results[0].semantic_type == "magnification"
        assert results[0].value == 50000
        assert results[0].unit == "x"

    def test_combined_prefix_and_suffix_not_duplicated(self):
        """「倍率」キーワードと「で観察」文脈語の両方を満たす表現でも、
        重複抽出 (包含関係にある一致の重複) が起きないことを確認する。"""
        results = self.interp.extract("倍率50,000倍で観察した試料の像。")
        magnification_results = [r for r in results if r.semantic_type == "magnification"]
        assert len(magnification_results) == 1
        assert magnification_results[0].value == 50000


class TestMagnificationExcludedPatterns:
    """design docで明示的に「初期対応から除外」とされた表現が、
    抽出されないことを確認する (誤検出防止の中核となる回帰テスト)。"""

    def setup_method(self):
        self.interp = SemTemTextRuleInterpreter()

    def test_bare_roman_x_suffix_excluded(self):
        results = self.interp.extract("50000xで撮影した画像。")
        assert results == []

    def test_bare_roman_x_prefix_excluded(self):
        results = self.interp.extract("x50000の設定で撮影した。")
        assert results == []

    def test_dimension_like_expression_excluded(self):
        results = self.interp.extract("試料サイズは10 x 20 mmだった。")
        magnification_results = [r for r in results if r.semantic_type == "magnification"]
        assert magnification_results == []

    def test_generic_variable_multiplication_excluded(self):
        results = self.interp.extract("A x Bの関係を確認した。")
        assert results == []

    def test_bare_bai_without_keyword_or_context_excluded(self):
        """観察文脈語も「倍率」キーワードも無い、単なる「数値+倍」
        (希釈倍率等、SEM/TEM分野に限らない一般的用法と混同しうる) は除外する。"""
        results = self.interp.extract("試料を3倍に希釈した。")
        magnification_results = [r for r in results if r.semantic_type == "magnification"]
        assert magnification_results == []


class TestKeywordlessQuantitiesAreNotExtracted:
    """意味キーワードを伴わない単位・数値のみの表現は、一切抽出しない
    (意味種別を確定できないものを候補化しない、という保守的な方針の確認)。"""

    def setup_method(self):
        self.interp = SemTemTextRuleInterpreter()

    def test_bare_kv_not_extracted(self):
        assert self.interp.extract("今回は5 kVで実施した。") == []

    def test_bare_nm_not_extracted(self):
        assert self.interp.extract("スケールバーは10 nmを示す。") == []

    def test_bare_pa_not_extracted(self):
        assert self.interp.extract("電流値は100 pA程度だった。") == []

    def test_none_text(self):
        assert self.interp.extract(None) == []

    def test_empty_text(self):
        assert self.interp.extract("") == []


class TestMultipleValuesInOneText:
    def test_extracts_in_order_of_appearance(self):
        interp = SemTemTextRuleInterpreter()
        text = "加速電圧 5 kV、WD 8 mmで、倍率50,000倍にて観察した。"
        results = interp.extract(text)
        semantic_types = [r.semantic_type for r in results]
        assert semantic_types == ["accelerating_voltage", "working_distance", "magnification"]


class TestNormalizeSemTemUnit:
    def test_known_unit(self):
        assert normalize_sem_tem_unit("accelerating_voltage", "KV") == "kV"

    def test_unknown_semantic_type_passthrough(self):
        assert normalize_sem_tem_unit("unknown_type", "xyz") == "xyz"

    def test_none_raw_unit(self):
        assert normalize_sem_tem_unit("accelerating_voltage", None) is None


class TestConfidencePolicyIntegration:
    """`policy.py`側の変更無しに、既存の`free_text_regex`ポリシー
    (confidence=0.95) がSEM/TEMプロファイルの抽出結果にもそのまま
    適用されることを確認する。"""

    def test_extracted_value_confidence_stays_one(self):
        interp = SemTemTextRuleInterpreter()
        extracted = interp.extract("加速電圧 5 kVで観察した。")[0]
        assert extracted.confidence == 1.0

    def test_candidate_confidence_becomes_point_nine_five(self):
        interp = SemTemTextRuleInterpreter()
        extracted = interp.extract("加速電圧 5 kVで観察した。")[0]
        candidate = candidate_from_extracted_value(extracted, context="sem_acquisition")
        assert candidate.source == "free_text_regex"
        assert candidate.confidence == 0.95
        assert candidate.semantic_type == "accelerating_voltage"
        assert candidate.context == "sem_acquisition"
        # role/targetは未設定 (pipeline.pyのpartition_candidates()が
        # unclassifiedへ振り分ける対象になる)。
        assert candidate.role is None
        assert candidate.target is None
