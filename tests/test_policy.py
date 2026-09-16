"""
`policy.py` (DEFAULT_SOURCE_CONFIDENCE / candidate_from_extracted_value) の
単体テスト。

「ExtractedValue.confidence(抽出ルールの一致確実性)」と
「InterpretationCandidate.confidence(採用候補としての確度)」は別の意味であり、
後者は情報源ごとのポリシー (DEFAULT_SOURCE_CONFIDENCE) から明示的に決める、
という設計の確認。
"""
from elabftw2maiml.interpretation import (
    DEFAULT_SOURCE_CONFIDENCE,
    ExtractedValue,
    candidate_confidence_for_source,
    candidate_from_extracted_value,
)


def test_default_source_confidence_for_custom_field_is_full_confidence():
    assert candidate_confidence_for_source("custom_field") == 1.0


def test_default_source_confidence_for_free_text_regex_is_below_one():
    assert DEFAULT_SOURCE_CONFIDENCE["free_text_regex"] == 0.95
    assert candidate_confidence_for_source("free_text_regex") == 0.95


def test_unknown_source_defaults_to_full_confidence():
    """ポリシー未定義の情報源を、ポリシー側の都合で不当に低評価しない。"""
    assert candidate_confidence_for_source("some_future_source") == 1.0


def test_candidate_from_extracted_value_uses_regex_source_and_policy_confidence():
    extracted = ExtractedValue(
        semantic_type="temperature",
        value=40,
        unit="degC",
        source_text="40℃で30分加熱した。",
        method="regex",
        confidence=1.0,
    )

    candidate = candidate_from_extracted_value(extracted)

    assert candidate.semantic_type == "temperature"
    assert candidate.value == 40
    assert candidate.unit == "degC"
    assert candidate.source == "free_text_regex"
    # 抽出自体の確実性(1.0) x 情報源としての確度(0.95) = 0.95
    assert candidate.confidence == 0.95
    assert candidate.source_text == "40℃で30分加熱した。"


def test_candidate_from_extracted_value_multiplies_extraction_confidence():
    """抽出処理自体の確実性が1.0未満の場合(将来のLLM抽出等)は、
    情報源ポリシーとの積になる。"""
    extracted = ExtractedValue(
        semantic_type="temperature",
        value=40,
        unit="degC",
        source_text="around 40C or so",
        method="regex",
        confidence=0.8,
    )

    candidate = candidate_from_extracted_value(extracted)

    assert candidate.confidence == 0.8 * 0.95


def test_candidate_from_extracted_value_context_is_passed_through():
    extracted = ExtractedValue(
        semantic_type="temperature",
        value=40,
        unit="degC",
        source_text="Step1: 40℃で加熱した。",
    )

    candidate = candidate_from_extracted_value(extracted, context="step:1")

    assert candidate.context == "step:1"


def test_candidate_from_extracted_value_allows_source_override():
    extracted = ExtractedValue(
        semantic_type="temperature",
        value=40,
        unit="degC",
        source_text="40℃",
        method="llm",
    )

    candidate = candidate_from_extracted_value(extracted, source="free_text_llm")

    assert candidate.source == "free_text_llm"
    # "free_text_llm" はまだ DEFAULT_SOURCE_CONFIDENCE に無いため、
    # ポリシー未定義の情報源として1.0が使われる。
    assert candidate.confidence == 1.0


def test_candidate_from_extracted_value_allows_explicit_source_confidence_override():
    extracted = ExtractedValue(
        semantic_type="temperature",
        value=40,
        unit="degC",
        source_text="40℃",
    )

    candidate = candidate_from_extracted_value(extracted, source_confidence=0.5)

    assert candidate.confidence == 0.5
