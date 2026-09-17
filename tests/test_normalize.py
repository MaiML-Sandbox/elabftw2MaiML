"""`elabftw2maiml/interpretation/normalize.py` (Phase 5-3 fix: 構造化フィールドの
値を競合判定前に正規化する) の単体テスト。

ユーザーから提示された対応表を、そのまま回帰テストとして固定する:

| Extra Field値 | 正規化結果 |
| --- | --- |
| "200 kV" | 200 kV |
| "200kV" | 200 kV |
| "200" | 対応表の単位を補い200 kV |
| 200 (数値) | 対応表の単位を補い200 kV |
| "200000 V" | (初期実装では単位換算をしないため) 正規化できない扱い |
| "200 mA" (期待単位kV) | 次元が異なるため正規化できない扱い |
| "not measured" (data_type number) | 数値として解釈できないため正規化できない扱い |
"""
from decimal import Decimal

from elabftw2maiml.interpretation.normalize import (
    NormalizedValue,
    canonical_unit,
    parse_numeric_with_unit,
)


class TestCanonicalUnit:
    def test_none_returns_none(self):
        assert canonical_unit(None) is None

    def test_empty_string_returns_none(self):
        assert canonical_unit("") is None
        assert canonical_unit("   ") is None

    def test_strips_whitespace(self):
        assert canonical_unit("  kV  ") == "kV"

    def test_micro_prefix_aliases_unify(self):
        assert canonical_unit("µm") == "um"
        assert canonical_unit("μm") == "um"
        assert canonical_unit("um") == "um"

    def test_degree_aliases_unify(self):
        assert canonical_unit("°") == "deg"
        assert canonical_unit("度") == "deg"
        assert canonical_unit("deg") == "deg"

    def test_unknown_unit_passthrough(self):
        assert canonical_unit("kV") == "kV"
        assert canonical_unit("nm") == "nm"


class TestParseNumericWithUnitFromUserTable:
    """モジュールdocstring / 依頼内で示された表の各行を、そのままテストケース化する。"""

    def test_number_and_unit_with_space(self):
        result = parse_numeric_with_unit("200 kV", expected_unit="kV")
        assert result == NormalizedValue(value=Decimal("200"), unit="kV", raw_value="200 kV")

    def test_number_and_unit_without_space(self):
        result = parse_numeric_with_unit("200kV", expected_unit="kV")
        assert result == NormalizedValue(value=Decimal("200"), unit="kV", raw_value="200kV")

    def test_bare_number_string_gets_expected_unit_filled_in(self):
        result = parse_numeric_with_unit("200", expected_unit="kV")
        assert result == NormalizedValue(value=Decimal("200"), unit="kV", raw_value="200")

    def test_bare_numeric_value_gets_expected_unit_filled_in(self):
        result = parse_numeric_with_unit(200, expected_unit="kV")
        assert result == NormalizedValue(value=Decimal("200"), unit="kV", raw_value=200)

    def test_float_value_gets_expected_unit_filled_in(self):
        result = parse_numeric_with_unit(0.204, expected_unit="nm")
        assert result == NormalizedValue(value=Decimal("0.204"), unit="nm", raw_value=0.204)

    def test_mismatched_unit_prefix_is_not_converted(self):
        """"200000 V" は次元としては加速電圧と合っているが、単位換算 (V->kV) は
        初期実装のスコープ外のため、正規化できないものとして扱う。"""
        result = parse_numeric_with_unit("200000 V", expected_unit="kV")
        assert result is None

    def test_mismatched_dimension_is_not_converted(self):
        """"200 mA" (電流) は期待単位 "kV" (電圧) と次元が異なる。"""
        result = parse_numeric_with_unit("200 mA", expected_unit="kV")
        assert result is None

    def test_non_numeric_string_cannot_be_parsed(self):
        result = parse_numeric_with_unit("not measured", expected_unit="kV")
        assert result is None

    def test_conflicting_free_text_value_still_parses_on_its_own(self):
        """"250 kV" 自体は正しく解析できる (競合検出自体は呼び出し側 (conflict.py)
        の責務であり、このモジュールは単に正規化するだけであることの確認)。"""
        result = parse_numeric_with_unit("250 kV", expected_unit="kV")
        assert result == NormalizedValue(value=Decimal("250"), unit="kV", raw_value="250 kV")


class TestParseNumericWithUnitEdgeCases:
    def test_no_expected_unit_skips_dimension_check(self):
        """期待単位が指定されていない場合、単位の食い違いチェックは行わない
        (対応表がそもそも単位を期待していないフィールド向け)。"""
        result = parse_numeric_with_unit("42 widgets", expected_unit=None)
        assert result == NormalizedValue(value=Decimal("42"), unit="widgets", raw_value="42 widgets")

    def test_no_unit_at_all_and_no_expected_unit(self):
        result = parse_numeric_with_unit("42", expected_unit=None)
        assert result == NormalizedValue(value=Decimal("42"), unit=None, raw_value="42")

    def test_negative_number(self):
        result = parse_numeric_with_unit("-15 deg", expected_unit="deg")
        assert result.value == Decimal("-15")

    def test_decimal_value_passthrough(self):
        result = parse_numeric_with_unit(Decimal("3.14"), expected_unit="nm")
        assert result.value == Decimal("3.14")

    def test_boolean_is_not_normalized(self):
        # bool は int のサブクラスだが、チェックボックス等の意味的な値であり
        # 数値+単位としての正規化対象ではない。
        assert parse_numeric_with_unit(True, expected_unit="kV") is None

    def test_none_value_is_not_normalized(self):
        assert parse_numeric_with_unit(None, expected_unit="kV") is None

    def test_micro_unit_alias_matches_expected_canonical_unit(self):
        result = parse_numeric_with_unit("40 µm", expected_unit="um")
        assert result == NormalizedValue(value=Decimal("40"), unit="um", raw_value="40 µm")

    def test_empty_string_cannot_be_parsed(self):
        assert parse_numeric_with_unit("", expected_unit="kV") is None


class TestNumericFormatSupport:
    """コードレビュー (2026-09-17) 5.2対応: 桁区切りカンマ・先頭`+`符号・
    指数表記の数値をparse_numeric_with_unit()が正しく解釈できること。"""

    def test_leading_plus_sign(self):
        result = parse_numeric_with_unit("+200", expected_unit="kV")
        assert result == NormalizedValue(value=Decimal("200"), unit="kV", raw_value="+200")

    def test_leading_plus_sign_with_embedded_unit(self):
        result = parse_numeric_with_unit("+200 kV", expected_unit="kV")
        assert result.value == Decimal("200")
        assert result.unit == "kV"

    def test_comma_thousands_separator(self):
        result = parse_numeric_with_unit("1,234", expected_unit=None)
        assert result == NormalizedValue(value=Decimal("1234"), unit=None, raw_value="1,234")

    def test_comma_thousands_separator_with_decimal(self):
        result = parse_numeric_with_unit("12,345.6", expected_unit=None)
        assert result.value == Decimal("12345.6")

    def test_comma_thousands_separator_with_unit(self):
        result = parse_numeric_with_unit("1,234 nm", expected_unit="nm")
        assert result.value == Decimal("1234")
        assert result.unit == "nm"

    def test_multiple_comma_groups(self):
        result = parse_numeric_with_unit("1,234,567", expected_unit=None)
        assert result.value == Decimal("1234567")

    def test_invalid_comma_grouping_is_not_normalized(self):
        """3桁ごとの区切りになっていない不正なカンマ位置は数値として認識しない。"""
        assert parse_numeric_with_unit("1,23", expected_unit=None) is None

    def test_exponent_notation_lowercase_e(self):
        result = parse_numeric_with_unit("1.5e-3", expected_unit=None)
        assert result.value == Decimal("1.5e-3")

    def test_exponent_notation_uppercase_e_with_plus(self):
        result = parse_numeric_with_unit("2.5E+10", expected_unit=None)
        assert result.value == Decimal("2.5E+10")

    def test_exponent_notation_with_unit(self):
        result = parse_numeric_with_unit("2e3 kV", expected_unit="kV")
        assert result.value == Decimal("2e3")
        assert result.unit == "kV"

    def test_exponent_and_comma_do_not_break_dimension_mismatch_check(self):
        """新しい数値表記でも、次元不一致の検出 (単位換算はしない方針) は
        従来通り機能する。"""
        assert parse_numeric_with_unit("1,234 mA", expected_unit="kV") is None
