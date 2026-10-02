"""
見出しの操作意味を解釈する Operation Interpreter
(elabftw2MaiML_section_semantic_review_fixes.md 2節)。

「鉄をSEM計測する」のような自然文の見出しは、`SectionDetector` で見出しとして保持
されるが、それだけでは何の操作かが分からない。このモジュールは見出し文字列から

    operation = sem_measurement   (実験操作の意味)
    object    = iron              (操作の対象)

を、YAML (`operation_mappings/*.yaml`) のキーワード辞書で取り出す。

`section_type` (文書構造上の大分類: observation/fixation/...) とは別概念であり、
`SectionClassifier` とも責務を分ける。ただし見出しが辞書に無いときに限り、操作に
結び付けた `section_type` (YAMLの `section_type`) を分類のヒントとして使える
(`SectionClassifier(mapping, operation_interpreter=...)`)。

初期実装はルールベースのみ。LLMによる解釈は将来拡張。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .sections import UNKNOWN_SECTION_TYPE, SectionMappingError


class OperationMappingError(SectionMappingError):
    """Operation Mapping 設定の検証エラー。"""


@dataclass(frozen=True)
class OperationInterpretation:
    """見出しから読み取った操作 (設計 推奨モデル)。

    operation:   操作の識別子 (例: "sem_measurement")
    object:      操作の対象 (例: "iron")。辞書に無ければ None
    source_text: 解釈元の見出し (原文)
    confidence:  ルールの一致は常に 1.0
    """

    operation: str
    object: Optional[str] = None
    source_text: Optional[str] = None
    confidence: float = 1.0


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).lower().strip())


def _find(haystack: str, needle: str) -> int:
    """`needle` の最初の出現位置 (無ければ -1)。ASCII英数字だけの語は、前後が
    英数字でない場合のみ一致とみなす ("Fe" が "Feature" に一致しないように)。"""
    if not needle:
        return -1
    if re.fullmatch(r"[a-z0-9_\-]+", needle):
        m = re.search(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])", haystack)
        return m.start() if m else -1
    return haystack.find(needle)


class OperationMapping:
    """見出しのキーワード -> operation、および対象語の alias -> object の辞書。

    YAML例:

        version: 1
        operations:
          sem_measurement:
            keywords: [SEM計測, SEM観察, 走査電子顕微鏡, SEM]
            section_type: observation      # 任意: 見出しが辞書に無い時の分類ヒント
        objects:
          iron: [鉄, Fe, iron]
    """

    def __init__(
        self,
        operations: Dict[str, Tuple[Tuple[str, ...], Optional[str]]],
        objects: Dict[str, Tuple[str, ...]],
    ):
        self._operations = dict(operations)
        self._objects = dict(objects)
        # (正規化キーワード, operation) を長い順に
        self._keywords: List[Tuple[str, str]] = sorted(
            ((_norm(k), op) for op, (kws, _st) in operations.items() for k in kws),
            key=lambda p: -len(p[0]),
        )
        self._aliases: List[Tuple[str, str]] = sorted(
            ((_norm(a), obj) for obj, al in objects.items() for a in al),
            key=lambda p: -len(p[0]),
        )

    @classmethod
    def from_dict(cls, data: dict) -> "OperationMapping":
        version = data.get("version", 1)
        if version != 1:
            raise OperationMappingError(f"未対応の version です: {version!r}")
        raw_ops = data.get("operations")
        if not isinstance(raw_ops, dict) or not raw_ops:
            raise OperationMappingError("operations が空、または辞書ではありません")
        ops: Dict[str, Tuple[Tuple[str, ...], Optional[str]]] = {}
        seen: Dict[str, str] = {}
        for name, spec in raw_ops.items():
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", str(name)):
                raise OperationMappingError(f"operation 名が不正です: {name!r}")
            if not isinstance(spec, dict) or set(spec) - {"keywords", "section_type"}:
                raise OperationMappingError(
                    f"{name}: 定義は keywords / section_type のみを持つ辞書にしてください")
            kws = spec.get("keywords")
            if not isinstance(kws, list) or not kws or not all(
                    isinstance(k, str) and _norm(k) for k in kws):
                raise OperationMappingError(f"{name}: keywords は空でない文字列のリストにしてください")
            st = spec.get("section_type")
            if st is not None and (not isinstance(st, str) or st == UNKNOWN_SECTION_TYPE):
                raise OperationMappingError(f"{name}: section_type が不正です: {st!r}")
            for k in kws:
                other = seen.get(_norm(k))
                if other is not None and other != name:
                    raise OperationMappingError(
                        f"キーワード {k!r} が複数の operation ({other!r}, {name!r}) に定義されています")
                seen[_norm(k)] = name
            ops[str(name)] = (tuple(kws), st)

        raw_objs = data.get("objects") or {}
        if not isinstance(raw_objs, dict):
            raise OperationMappingError("objects は辞書で指定してください")
        objs: Dict[str, Tuple[str, ...]] = {}
        for name, aliases in raw_objs.items():
            if not isinstance(aliases, list) or not aliases or not all(
                    isinstance(a, str) and _norm(a) for a in aliases):
                raise OperationMappingError(f"objects.{name}: 空でない文字列のリストにしてください")
            objs[str(name)] = tuple(aliases)
        return cls(ops, objs)

    @classmethod
    def from_yaml_file(cls, path: str) -> "OperationMapping":
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - 環境依存
            raise ImportError("pyyaml が必要です。`pip install pyyaml` を実行してください。") from exc
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise OperationMappingError(f"{path}: YAMLのトップレベルが辞書ではありません")
        return cls.from_dict(data)

    @property
    def operations(self) -> List[str]:
        return list(self._operations)

    def section_type_for(self, operation: str) -> Optional[str]:
        entry = self._operations.get(operation)
        return entry[1] if entry else None

    def match_operation(self, text: str) -> Optional[str]:
        """最も長いキーワードに一致した operation (無ければ None)。"""
        norm = _norm(text)
        for keyword, op in self._keywords:
            if _find(norm, keyword) >= 0:
                return op
        return None

    def match_object(self, text: str) -> Optional[str]:
        """見出し中で最初に現れる対象語の object (無ければ None)。"""
        norm = _norm(text)
        best: Optional[Tuple[int, int, str]] = None
        for alias, obj in self._aliases:
            pos = _find(norm, alias)
            if pos >= 0 and (best is None or (pos, -len(alias)) < (best[0], best[1])):
                best = (pos, -len(alias), obj)
        return best[2] if best else None


class OperationInterpreter:
    """見出し文字列から `OperationInterpretation` を作る。"""

    def __init__(self, mapping: OperationMapping):
        self._mapping = mapping

    def interpret(self, title: Optional[str]) -> Optional[OperationInterpretation]:
        """操作が読み取れなければ None (対象語だけの見出しは操作とみなさない)。"""
        if not title or not title.strip():
            return None
        operation = self._mapping.match_operation(title)
        if operation is None:
            return None
        return OperationInterpretation(
            operation=operation,
            object=self._mapping.match_object(title),
            source_text=title,
        )

    def section_type_for(self, operation: str) -> Optional[str]:
        return self._mapping.section_type_for(operation)
