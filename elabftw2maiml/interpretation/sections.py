"""
実験本文のセクション分割・分類 (elabftw2MaiML_section_detection_classification_design.md)。

実験本文の解析を3段階に分ける。

    1. Section Detection      (`SectionDetector`)   : 見出しかどうかを判定して本文を分割する
    2. Section Classification (`SectionClassifier`) : 見出しを標準の `section_type` へ分類する
    3. Semantic Interpretation (既存のInterpreter)  : セクション本文から値を抽出する

「見出しかどうか」と「その見出しが何を意味するか」は別処理とする。見出し文字列を
工程名へ固定的に対応付けるのではなく、`SectionMapping` (YAML) の alias 辞書で
分類し、辞書に無い見出しは `section_type="unknown"` として本文ごと保持する
(未知の見出しだから本文を捨てる、ということはしない)。

`section_type` は文書構造上の大分類であり、MaiMLの `role` とは別概念
(設計 「section_type と role は別概念」)。このモジュールは `role`/`target` を
一切決めない。セクションは、候補の `context` (`section:<type>:<index>`) を
決めるためだけに使う。

初期実装の範囲 (設計 Phase A〜D): 検出・分類・パイプライン接続・未知セクションの
レポート。同一セクション内の複数処理の分割 (Statement)・考察文の分類は未実装。
"""
from __future__ import annotations

import html as _html
import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

UNKNOWN_SECTION_TYPE = "unknown"
# セクションより前 (最初の見出しの前) の本文。見出しが無いので分類せず、
# 従来通りの context ("experiment") のまま扱う。
PREAMBLE_CONTEXT = "experiment"


class SectionMappingError(ValueError):
    """Section Mapping 設定の検証エラー。"""


# ---------------------------------------------------------------------------
# モデル
# ---------------------------------------------------------------------------

@dataclass
class TextSection:
    """本文中の、見出しで区切られた1つのブロック (設計 4節)。

    key:       内部で一意な識別子 ("section:1", ...)。前置部分は "section:0"
    title:     原文の見出し (標準化後も保持する)。前置部分は ""
    body:      見出しに属する本文
    index:     本文中の出現順 (前置部分は0、見出しは1から)
    level:     見出しの階層 (1=大見出し)。Markdown "#" で取得でき、無ければ1
    parent_id: 親セクションの key
    section_type: 分類後の標準カテゴリ (未分類は None、辞書に無ければ "unknown")
    context:   分類後に決まる context 文字列 (階層pathを含む表示・候補用)
    type_index: 同じ section_type の中での通し番号 (washing:1, washing:2, ...)
    """

    key: str
    title: str
    body: str
    index: int
    level: int = 1
    parent_id: Optional[str] = None
    section_type: Optional[str] = None
    context: Optional[str] = None
    type_index: int = 0

    @property
    def is_preamble(self) -> bool:
        return self.index == 0


# ---------------------------------------------------------------------------
# 見出しの正規化
# ---------------------------------------------------------------------------

_TRAILING_COLON_RE = re.compile(r"[\s:：]+$")
# 末尾の連番 ("固定1" / "Fixation 2" / "洗浄(3)")。
_TRAILING_NUMBER_RE = re.compile(r"[\s_\-]*[\(（]?\d+[\)）]?$")
_COMPACT_DROP_RE = re.compile(r"[\s\-_‐‑–—・･]+")


def normalize_heading(text: str) -> str:
    """見出しを比較用に正規化する: 全角/半角統一 (NFKC)・小文字化・末尾コロン除去・
    余分な空白の整理 (設計 「見出しの正規化」)。連番は取り除かない
    (`strip_trailing_number()` を別に呼ぶ)。"""
    s = unicodedata.normalize("NFKC", text).lower().strip()
    s = _TRAILING_COLON_RE.sub("", s)
    return re.sub(r"\s+", " ", s)


def strip_trailing_number(text: str) -> str:
    stripped = _TRAILING_NUMBER_RE.sub("", text)
    return stripped if stripped else text


def compact_heading(text: str) -> str:
    """空白・ハイフン等も除いた比較キー ("Pre Fixation"/"pre-fixation" -> "prefixation")。"""
    return _COMPACT_DROP_RE.sub("", normalize_heading(text))


# ---------------------------------------------------------------------------
# Section Mapping (YAML)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SectionRule:
    section_type: str
    headings: Tuple[str, ...]


class SectionMapping:
    """見出しの alias -> section_type の辞書 (設計 「Section Mapping YAML」)。

    YAML例:

        version: 1            # 省略可
        section_types:
          fixation:
            headings: [固定, 前固定, fixation, pre-fixation]
    """

    def __init__(self, rules: Dict[str, SectionRule]):
        self._rules = dict(rules)
        self._by_compact: Dict[str, str] = {}
        # 先頭一致用: (空白を保った正規化alias, section_type)。長いaliasを先に試す。
        prefix: List[Tuple[str, str]] = []
        for rule in self._rules.values():
            self._validate_rule(rule)
            for heading in rule.headings:
                key = compact_heading(strip_trailing_number(normalize_heading(heading)))
                other = self._by_compact.get(key)
                if other is not None and other != rule.section_type:
                    raise SectionMappingError(
                        f"見出し {heading!r} が複数の section_type "
                        f"({other!r}, {rule.section_type!r}) に定義されています"
                    )
                self._by_compact[key] = rule.section_type
                prefix.append((strip_trailing_number(normalize_heading(heading)),
                               rule.section_type))
        self._prefix = sorted(prefix, key=lambda p: -len(p[0]))

    @staticmethod
    def _validate_rule(rule: SectionRule) -> None:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", rule.section_type):
            raise SectionMappingError(
                f"section_type 名が不正です: {rule.section_type!r} (英数字と_のみ)"
            )
        if rule.section_type == UNKNOWN_SECTION_TYPE:
            raise SectionMappingError(
                f"section_type {UNKNOWN_SECTION_TYPE!r} は予約語です (辞書に無い見出しに自動で付きます)"
            )
        if not rule.headings:
            raise SectionMappingError(f"{rule.section_type}: headings が空です")
        for h in rule.headings:
            if not isinstance(h, str) or not compact_heading(h):
                raise SectionMappingError(
                    f"{rule.section_type}: 空の見出し定義があります: {h!r}"
                )

    @classmethod
    def from_dict(cls, data: dict) -> "SectionMapping":
        version = data.get("version", 1)
        if version != 1:
            raise SectionMappingError(f"未対応の version です: {version!r}")
        raw = data.get("section_types")
        if not isinstance(raw, dict) or not raw:
            raise SectionMappingError("section_types が空、または辞書ではありません")
        rules: Dict[str, SectionRule] = {}
        for name, spec in raw.items():
            if not isinstance(spec, dict):
                raise SectionMappingError(f"{name}: 定義が辞書ではありません")
            unknown = set(spec) - {"headings"}
            if unknown:
                raise SectionMappingError(f"{name}: 未知のキー {sorted(unknown)} があります")
            headings = spec.get("headings")
            if not isinstance(headings, (list, tuple)) or not all(
                isinstance(h, str) for h in headings
            ):
                raise SectionMappingError(f"{name}: headings は文字列のリストで指定してください")
            rules[str(name)] = SectionRule(str(name), tuple(headings))
        return cls(rules)

    @classmethod
    def from_yaml_file(cls, path: str) -> "SectionMapping":
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - 環境依存
            raise ImportError("pyyaml が必要です。`pip install pyyaml` を実行してください。") from exc
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise SectionMappingError(f"{path}: YAMLのトップレベルが辞書ではありません")
        return cls.from_dict(data)

    @property
    def rules(self) -> Dict[str, SectionRule]:
        return dict(self._rules)

    def lookup_heading(self, heading: str) -> Optional[str]:
        """見出し (正規化・連番除去後に完全一致) から section_type を引く。無ければ None。"""
        key = compact_heading(strip_trailing_number(normalize_heading(heading)))
        return self._by_compact.get(key)

    def match_heading(self, heading: str) -> Optional[Tuple[str, str]]:
        """`(section_type, kind)` を返す。kind は "exact" (完全一致) または
        "prefix" (alias の直後に空白・括弧が続く先頭一致。例: "細胞培養 in KK2")。
        どちらにも該当しなければ None。"""
        exact = self.lookup_heading(heading)
        if exact is not None:
            return exact, "exact"
        spaced = strip_trailing_number(normalize_heading(heading))
        for alias, section_type in self._prefix:
            if spaced.startswith(alias) and len(spaced) > len(alias) \
                    and spaced[len(alias)] in " (":
                return section_type, "prefix"
        return None


# ---------------------------------------------------------------------------
# HTML -> 構造を保ったテキスト
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def html_to_structured_text(html: Optional[str]) -> Optional[str]:
    """eLabFTWの本文 (HTMLまたはMarkdown) を、行・段落・見出しの構造を保った
    プレーンテキストにする。セクション検出専用。

    `elabftw_client._strip_html()` は全てのタグ・改行を1つの空白にまとめるため、
    見出しや段落の区切りが失われる。ここでは次のように変換する:

    - `<h1>`〜`<h6>` -> `# `〜`###### ` 付きの行 (Markdownの見出し記法)
    - 太字だけの段落 `<p><strong>…</strong></p>` -> `**…**` の行
    - `<br>`・ブロック要素の終端 -> 改行。空の段落 (`<p>&nbsp;</p>`) は空行になる
    - 行内の連続空白は1つにし、3行以上の連続改行は空行1つにまとめる

    タグを含まないテキスト (MarkdownやプレーンテキストのMarkdown本文) はそのまま
    行構造を保つ。
    """
    if not html:
        return None
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", html)

    def heading(m: "re.Match[str]") -> str:
        inner = _TAG_RE.sub("", m.group(2)).strip()
        return f"\n\n{'#' * int(m.group(1))} {inner}\n\n" if inner else "\n"

    s = re.sub(r"(?is)<h([1-6])[^>]*>(.*?)</h\1>", heading, s)

    def bold_paragraph(m: "re.Match[str]") -> str:
        inner = _TAG_RE.sub("", m.group(2)).strip()
        return f"\n**{inner}**\n" if inner else "\n"

    s = re.sub(r"(?is)<p[^>]*>\s*<(strong|b)[^>]*>(.*?)</\1>\s*</p>", bold_paragraph, s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|tr|ul|ol|table|blockquote|pre)\s*>", "\n", s)
    s = _TAG_RE.sub("", s)
    s = _html.unescape(s).replace("\xa0", " ")

    lines = [re.sub(r"[ \t　]+", " ", line).strip() for line in s.splitlines()]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text or None


# ---------------------------------------------------------------------------
# Section Detection
# ---------------------------------------------------------------------------

_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_BOLD_LINE_RE = re.compile(r"^\*\*(.+?)\*\*$")
# 数値+単位らしい表現 ("20 min" / "4℃" / "1%")。数字だけの連番 ("固定1") は含まない。
_NUMBER_UNIT_RE = re.compile(r"\d\s*[A-Za-zµμ℃°%％]")
_KEY_VALUE_CHARS = set(":：=＝@＠、。")


class SectionDetector:
    """本文を行単位で見て、見出しらしい行で区切って `TextSection` に分割する。
    意味分類は行わない (設計 5節)。

    見出しらしさはスコアで判定する (設計 7節)。

        +3 既知の見出し alias に完全一致 (`mapping` 指定時)
        +2 既知の見出し alias で始まる短い行 ("細胞培養 in KK2")
        +2 太字だけの行 (`**…**`)
        +1 直前が空行 (本文の先頭を含む)
        +1 文字数が短い (`max_title_length` 以下)
        -1 数値+単位を含む
        -2 `: = @ 、 。` を含む (キー・値や文章)

    スコアが `threshold` (既定2) 以上なら見出し。Markdownの `#` 見出しは常に見出し
    (階層レベルも取得する)。既知alias・太字・Markdownのいずれでもない行は、
    「短い (+1)」だけでは見出しにならず、直前が空行 (または本文の先頭) であることも必要
    (空行の無い本文で「KK2 on ice」のような行を見出しと誤認しないため)。

    限界: 空行で段落を分けている本文では、段落の先頭にある短い行
    (キー・値や数値を含まないもの) が見出しと判定されることがある。誤分割は
    変換レポートの「実験本文のセクション」で確認でき、`threshold`・`max_title_length`
    で調整できる。

    mapping: 既知の見出し辞書 (省略時は構造・短さ・空行だけで判定する)。
    """

    def __init__(
        self,
        mapping: Optional[SectionMapping] = None,
        *,
        max_title_length: int = 20,
        threshold: int = 2,
    ):
        self._mapping = mapping
        self._max_len = max_title_length
        self._threshold = threshold

    def _heading_of(
        self, lines: Sequence[str], i: int
    ) -> Optional[Tuple[str, int]]:
        """行 i が見出しなら `(title, level)`、そうでなければ None。"""
        raw = lines[i].strip()
        if not raw:
            return None
        md = _MD_HEADING_RE.match(raw)
        if md:
            return md.group(2).strip(), len(md.group(1))

        score = 0
        title = raw
        bold = _BOLD_LINE_RE.match(raw)
        if bold:
            title = bold.group(1).strip()
            score += 2
        title = _TRAILING_COLON_RE.sub("", title)
        if not title:
            return None

        norm = unicodedata.normalize("NFKC", title)
        short = len(norm) <= self._max_len
        if short:
            score += 1
        if i == 0 or not lines[i - 1].strip():
            score += 1
        if _NUMBER_UNIT_RE.search(norm):
            score -= 1
        if any(ch in _KEY_VALUE_CHARS for ch in norm):
            score -= 2

        if self._mapping is not None and short:
            matched = self._mapping.match_heading(title)
            if matched is not None:
                score += 3 if matched[1] == "exact" else 2

        return (title, 1) if score >= self._threshold else None

    def split(self, text: Optional[str]) -> List[TextSection]:
        """本文を `TextSection` のリストに分割する (出現順)。見出し前の本文は
        `index=0`・`title=""` の前置セクションとして保持する。本文が空なら空リスト。"""
        if not text or not text.strip():
            return []
        lines = text.splitlines()

        headings: Dict[int, Tuple[str, int]] = {}
        for i in range(len(lines)):
            found = self._heading_of(lines, i)
            if found is not None:
                headings[i] = found

        sections: List[TextSection] = []
        stack: List[TextSection] = []  # 親候補 (level の昇順)
        body_lines: List[str] = []
        current: Optional[TextSection] = None

        def flush_body() -> None:
            nonlocal body_lines
            if current is not None:
                current.body = "\n".join(body_lines).strip()
            body_lines = []

        preamble: List[str] = []
        for i, line in enumerate(lines):
            if i in headings:
                if current is None:
                    pre = "\n".join(preamble).strip()
                    if pre:
                        sections.append(TextSection(
                            key="section:0", title="", body=pre, index=0))
                else:
                    flush_body()
                title, level = headings[i]
                while stack and stack[-1].level >= level:
                    stack.pop()
                idx = len([s for s in sections if s.index > 0]) + 1
                current = TextSection(
                    key=f"section:{idx}", title=title, body="", index=idx,
                    level=level, parent_id=stack[-1].key if stack else None)
                sections.append(current)
                stack.append(current)
            elif current is None:
                preamble.append(line)
            else:
                body_lines.append(line)

        if current is None:
            return [TextSection(key="section:0", title="", body=text.strip(), index=0)]
        flush_body()
        return sections


# ---------------------------------------------------------------------------
# Section Classification
# ---------------------------------------------------------------------------

class SectionClassifier:
    """見出しを標準の `section_type` へ分類し、`context` を付ける。
    辞書に無い見出しは `unknown` として保持する (設計 「ユーザー固有見出しへの対応」)。"""

    def __init__(self, mapping: SectionMapping):
        self._mapping = mapping

    def classify(self, section: TextSection) -> TextSection:
        """1つのセクションに `section_type` だけを付けて返す (context・連番は
        セクション間の関係が要るため `classify_all()` で付ける)。"""
        if section.is_preamble:
            return replace(section, section_type=UNKNOWN_SECTION_TYPE)
        matched = self._mapping.match_heading(section.title)
        return replace(section, section_type=matched[0] if matched else UNKNOWN_SECTION_TYPE)

    def classify_all(self, sections: Sequence[TextSection]) -> List[TextSection]:
        """全セクションを分類し、同じ section_type の通し番号
        (`washing:1`, `washing:2`, ...) と階層 path の `context` を付ける
        (設計 「context の設計」)。同じ工程が複数回ある本文でも、context が分かれる
        ので、異なる工程の同種の値 (`20 min` と `30 min`) が競合しない。

        内部識別は `key` + `parent_id`、候補に付ける context は階層 path
        ("section:a:1/b:1")。前置セクションは従来通り "experiment"。"""
        counters: Dict[str, int] = {}
        out: List[TextSection] = []
        by_key: Dict[str, TextSection] = {}
        for section in sections:
            classified = self.classify(section)
            if classified.is_preamble:
                classified = replace(classified, context=PREAMBLE_CONTEXT)
            else:
                st = classified.section_type
                counters[st] = counters.get(st, 0) + 1
                segment = f"{st}:{counters[st]}"
                parent = by_key.get(classified.parent_id) if classified.parent_id else None
                path = (f"{parent.context}/{segment}" if parent and parent.context
                        else f"section:{segment}")
                classified = replace(classified, context=path, type_index=counters[st])
            by_key[classified.key] = classified
            out.append(classified)
        return out
