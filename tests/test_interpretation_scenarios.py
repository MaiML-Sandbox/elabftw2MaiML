"""
リリース時の基準ケース (release baseline scenarios)。

`interpretation/` パッケージ (structured.py Phase2 / text.py Phase3 /
conflict.py・policy.py Phase4) を一連のパイプラインとして通し、
今後の変更に対する回帰の基準として次の3ケースを固定する。

    1. 構造化フィールドのみ (自由記述からの候補が無い)
    2. 自由記述のみ (構造化フィールドからの候補が無い)
    3. 構造化フィールドと自由記述が矛盾する

いずれのケースも TextRuleInterpreter.extract() -> candidate_from_extracted_value()
-> detect_conflicts() という実際の呼び出し順序をそのまま使う (個々のモジュール
単体テストは test_text_rule_interpreter.py / test_conflict.py / test_policy.py に
別途ある。ここでは「組み合わせたときの挙動」を基準として固定する)。
"""
from elabftw2maiml.interpretation import (
    InterpretationCandidate,
    TextRuleInterpreter,
    candidate_from_extracted_value,
    detect_conflicts,
)

_interpreter = TextRuleInterpreter()


def test_scenario_structured_field_only():
    """構造化フィールド (Custom Field) からの値しか無い場合。
    比較対象が無いため、単独の候補があるだけで競合は検出されない。"""
    candidates = [
        InterpretationCandidate(
            semantic_type="temperature", value=50, unit="degC", source="custom_field",
        ),
    ]

    conflicts = detect_conflicts(candidates)

    assert conflicts == []


def test_scenario_free_text_only():
    """自由記述からの抽出しか無い場合。TextRuleInterpreter.extract() の実際の
    出力を candidate_from_extracted_value() 経由でそのまま使う。"""
    extracted_values = _interpreter.extract("40℃で30分加熱した。")
    assert len(extracted_values) == 2  # temperature, duration

    candidates = [candidate_from_extracted_value(v) for v in extracted_values]

    assert candidates[0].source == "free_text_regex"
    assert candidates[0].confidence == 0.95  # DEFAULT_SOURCE_CONFIDENCE["free_text_regex"]
    assert detect_conflicts(candidates) == []


def test_scenario_structured_and_free_text_conflict():
    """development plan 9節の例そのもの:
    Custom Field: Temperature = 50℃ / 自由記述: 「40℃で30分加熱した。」
    -> 一致しないため競合として検出され、どちらの値も上書きされずに残る。"""
    structured_candidate = InterpretationCandidate(
        semantic_type="temperature", value=50, unit="degC", source="custom_field",
    )

    extracted_temperature = next(
        v for v in _interpreter.extract("40℃で30分加熱した。") if v.semantic_type == "temperature"
    )
    free_text_candidate = candidate_from_extracted_value(extracted_temperature)

    candidates = [structured_candidate, free_text_candidate]
    conflicts = detect_conflicts(candidates)

    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict.semantic_type == "temperature"
    assert {c.source for c in conflict.candidates} == {"custom_field", "free_text_regex"}
    assert {c.value for c in conflict.candidates} == {50, 40}
    # 上書きされず、両方の値が候補として残っていることを確認する
    assert conflict.candidates == (structured_candidate, free_text_candidate)
