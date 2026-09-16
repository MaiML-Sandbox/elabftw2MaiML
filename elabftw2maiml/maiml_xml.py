"""
MaiML (JIS K 0200) の低レベルXML構築ユーティリティ。

各 build_* 関数は、アップロードされたXSD (maiml-*.xsd) および
MaiML_AI_Common_Specification.md 6.1節の「主要タグの出力順序早見表」に
定義された子要素の出現順序を厳密に守って <Element> を組み立てる。

グローバル要素の共通パターン (globalObjectContentGroup, maiml-core.xsd):
    <uuid> (必須)
    <insertion>* (0以上)
    <name>? <description>? <annotation>?   (このいずれかがある場合、encryptionは使わない)
    <property>* <content>*
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

from lxml import etree

NS_MAIML = "http://www.maiml.org/schemas"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
NS_DS = "http://www.w3.org/2000/09/xmldsig#"

QN_XSI_TYPE = f"{{{NS_XSI}}}type"


def E(tag: str, *children, **attrs) -> etree._Element:
    """MaiML名前空間の要素をattrs付きで作る簡易ヘルパー。"""
    el = etree.Element(f"{{{NS_MAIML}}}{tag}")
    for k, v in attrs.items():
        if v is None:
            continue
        el.set(k, str(v))
    for c in children:
        if c is None:
            continue
        el.append(c)
    return el


def text_el(tag: str, text: str, **attrs) -> etree._Element:
    el = E(tag, **attrs)
    el.text = text
    return el


# ---------------------------------------------------------------------------
# property / content (汎用データコンテナ)  -- 3.4節
# ---------------------------------------------------------------------------

_NUMERIC_ATTR_TYPES = {
    "decimalType", "doubleType", "floatType", "intType", "longType",
    "shortType", "byteType", "unsignedIntType", "unsignedLongType",
    "unsignedShortType", "unsignedByteType",
    "decimalListType", "doubleListType", "floatListType", "intListType",
    "longListType", "shortListType", "byteListType", "unsignedIntListType",
    "unsignedLongListType", "unsignedShortListType", "unsignedByteListType",
}
_DATETIME_ATTR_TYPES = {"dateTimeType", "dateTimeListType"}


def property_el(
    key: str,
    xsi_type: str,
    value: Optional[str] = None,
    values: Optional[Sequence[str]] = None,
    description: Optional[str] = None,
    format_string: Optional[str] = None,
    units: Optional[str] = None,
    scale_factor: Optional[str] = None,
) -> etree._Element:
    """
    <property xsi:type="..." key="...">...</property> を1つ作る。

    - value: 単一値 (string/decimal/int等)
    - values: 複数値 (List系のため空白区切りにまとめる)
    - formatString/units/scaleFactor は数値・日時系の型にのみ付与可能 (3.4.1節の表)
    """
    el = E("property", key=key)
    el.set(QN_XSI_TYPE, xsi_type)

    if xsi_type in _NUMERIC_ATTR_TYPES:
        if format_string is not None:
            el.set("formatString", format_string)
        if units is not None:
            el.set("units", units)
        if scale_factor is not None:
            el.set("scaleFactor", scale_factor)
    elif xsi_type in _DATETIME_ATTR_TYPES:
        if format_string is not None:
            el.set("formatString", format_string)

    if description is not None:
        el.append(text_el("description", description))

    if values is not None:
        el.append(text_el("value", " ".join(values)))
    elif value is not None:
        el.append(text_el("value", str(value)))

    return el


def content_el(
    key: str,
    xsi_type: str,
    values: Sequence[str],
    content_id: Optional[str] = None,
    axis: Optional[str] = None,
    size: Optional[int] = None,
    format_string: Optional[str] = None,
    units: Optional[str] = None,
    scale_factor: Optional[str] = None,
    ref: Optional[str] = None,
) -> etree._Element:
    """<content xsi:type="content...ListType" ...>軸付きベクトルデータ (3.4.3節)"""
    el = E("content", key=key, id=content_id, axis=axis, ref=ref)
    el.set(QN_XSI_TYPE, xsi_type)
    if size is not None:
        el.set("size", str(size))
    if format_string is not None:
        el.set("formatString", format_string)
    if units is not None:
        el.set("units", units)
    if scale_factor is not None:
        el.set("scaleFactor", scale_factor)
    el.append(text_el("value", " ".join(str(v) for v in values)))
    return el


def insertion_el(uri: str, file_hash_b64: str, hash_method: str = "SHA-256",
                  uuid_val: Optional[str] = None, fmt: Optional[str] = None) -> etree._Element:
    """外部ファイル参照 <insertion> (4.5節)。eLabFTWの添付ファイル参照に使用。"""
    el = E("insertion")
    el.append(text_el("uri", uri))
    hash_el = text_el("hash", file_hash_b64, method=hash_method)
    el.append(hash_el)
    if uuid_val:
        el.append(text_el("uuid", uuid_val))
    if fmt:
        el.append(text_el("format", fmt))
    return el


# ---------------------------------------------------------------------------
# globalObjectContentGroup: uuid, insertion*, name?, description?, annotation?,
#                            property*, content*
# ---------------------------------------------------------------------------

def global_content(
    uuid_val: str,
    insertions: Optional[Iterable[etree._Element]] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
    annotation: Optional[str] = None,
    properties: Optional[Iterable[etree._Element]] = None,
    contents: Optional[Iterable[etree._Element]] = None,
) -> list:
    """globalObjectContentGroup を構成する子要素のリストを、正しい順序で返す。"""
    out = [text_el("uuid", uuid_val)]
    for ins in insertions or []:
        out.append(ins)
    if name is not None:
        out.append(text_el("name", name))
    if description is not None:
        out.append(text_el("description", description))
    if annotation is not None:
        out.append(text_el("annotation", annotation))
    for p in properties or []:
        out.append(p)
    for c in contents or []:
        out.append(c)
    return out


def ref_el(tag: str, ref_id: str, elem_id: str) -> etree._Element:
    """placeRef / transitionRef / templateRef / instanceRef / *Ref 系の共通形。"""
    return E(tag, id=elem_id, ref=ref_id)
