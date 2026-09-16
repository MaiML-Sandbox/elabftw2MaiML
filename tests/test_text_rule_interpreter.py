"""
`TextRuleInterpreter` (自由記述からの温度・時間・質量・体積・回転数・pH抽出) の
単体テスト。elabftw2MaiML_phase1_development_plan.md 8節・13節に基づく。
"""
import pytest

from elabftw2maiml.interpretation import ExtractedValue, TextRuleInterpreter, normalize_unit

interpreter = TextRuleInterpreter()


def test_empty_or_none_text_returns_empty_list():
    assert interpreter.extract(None) == []
    assert interpreter.extract("") == []


def test_worked_example_from_development_plan():
    """development plan 8節の例そのもの:
    「40 ℃で30分加熱した。」-> temperature=40 degC, duration=30 min"""
    text = "40 ℃で30分加熱した。"
    values = interpreter.extract(text)

    assert values == [
        ExtractedValue(semantic_type="temperature", value=40, unit="degC",
                        source_text=text, method="regex", confidence=1.0),
        ExtractedValue(semantic_type="duration", value=30, unit="min",
                        source_text=text, method="regex", confidence=1.0),
    ]


@pytest.mark.parametrize("text,expected_unit", [
    ("40℃で撹拌した。", "degC"),
    ("40°Cで撹拌した。", "degC"),
    ("反応温度は300Kに保持した。", "K"),
])
def test_temperature_units(text, expected_unit):
    values = [v for v in interpreter.extract(text) if v.semantic_type == "temperature"]
    assert len(values) == 1
    assert values[0].value == (300 if expected_unit == "K" else 40)
    assert values[0].unit == expected_unit


@pytest.mark.parametrize("text,expected_value,expected_unit", [
    ("30分間撹拌した。", 30, "min"),
    ("30 min撹拌した。", 30, "min"),
    ("10秒間だけ静置した。", 10, "s"),
    ("10 sec静置した。", 10, "s"),
    ("2時間反応させた。", 2, "h"),
    ("2 h反応させた。", 2, "h"),
    ("1時間30分反応させた。", 1, "h"),  # 最初の一致 (h) のみ確認、後段のテストで複数一致を確認
])
def test_duration_units(text, expected_value, expected_unit):
    values = [v for v in interpreter.extract(text) if v.semantic_type == "duration"]
    assert len(values) >= 1
    assert values[0].value == expected_value
    assert values[0].unit == expected_unit


def test_duration_handles_multiple_values_in_one_sentence():
    text = "1時間30分反応させた。"
    values = [v for v in interpreter.extract(text) if v.semantic_type == "duration"]
    assert [(v.value, v.unit) for v in values] == [(1, "h"), (30, "min")]


@pytest.mark.parametrize("text,expected_value,expected_unit", [
    ("試料10 mgを秤量した。", 10, "mg"),
    ("試料10gを秤量した。", 10, "g"),
    ("試料1.5kgを秤量した。", 1.5, "kg"),
])
def test_mass_units(text, expected_value, expected_unit):
    values = [v for v in interpreter.extract(text) if v.semantic_type == "mass"]
    assert len(values) == 1
    assert values[0].value == expected_value
    assert values[0].unit == expected_unit


@pytest.mark.parametrize("text,expected_value,expected_unit", [
    ("メタノール1 mLに溶解した。", 1, "mL"),
    ("メタノール500µLを加えた。", 500, "uL"),
    ("メタノール500μLを加えた。", 500, "uL"),
    ("溶液を2 Lのフラスコに移した。", 2, "L"),
])
def test_volume_units(text, expected_value, expected_unit):
    values = [v for v in interpreter.extract(text) if v.semantic_type == "volume"]
    assert len(values) == 1
    assert values[0].value == expected_value
    assert values[0].unit == expected_unit


def test_rotation_speed():
    text = "1000 rpmで遠心分離した。"
    values = [v for v in interpreter.extract(text) if v.semantic_type == "rotation_speed"]
    assert len(values) == 1
    assert values[0].value == 1000
    assert values[0].unit == "rpm"


@pytest.mark.parametrize("text,expected_value", [
    ("pH 7に調整した。", 7),
    ("pH=7.0で反応させた。", 7.0),
    ("pH7の緩衝液を使用した。", 7),
])
def test_ph(text, expected_value):
    values = [v for v in interpreter.extract(text) if v.semantic_type == "ph"]
    assert len(values) == 1
    assert values[0].value == expected_value
    assert values[0].unit is None


def test_source_text_is_the_whole_input_not_just_the_match():
    text = "試料10 mgをメタノール1 mLに溶解し、40℃で30分加熱した。"
    values = interpreter.extract(text)
    assert len(values) >= 3
    for v in values:
        assert v.source_text == text


def test_results_are_ordered_by_position_in_text():
    text = "40℃で30分加熱後、1000 rpmで遠心分離し、pH 7に調整した。"
    values = interpreter.extract(text)
    assert [v.semantic_type for v in values] == ["temperature", "duration", "rotation_speed", "ph"]


def test_no_false_positive_when_unit_is_part_of_a_longer_word():
    # "5Lab" のように単位のように見えて実際は単位でない表記を誤って拾わない
    # ("L" の直後にアルファベットが続く場合は単位として扱わない)
    text = "5Labでは特に異常は無かった。"
    values = [v for v in interpreter.extract(text) if v.semantic_type == "volume"]
    assert values == []


def test_normalize_unit_passthrough_for_unknown_unit():
    assert normalize_unit("temperature", "℃") == "degC"
    assert normalize_unit("temperature", None) is None
    assert normalize_unit("mass", "斤") == "斤"  # テーブルに無い単位はそのまま返す
