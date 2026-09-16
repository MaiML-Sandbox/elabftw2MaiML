"""
eLabFTW から取得したデータを MaiML ビルダーに渡すための中間データモデル。

elabftw_client.py がこれらのdataclassを組み立て、maiml_builder.py が
これらを使ってXMLを構築する。ビルダー自体をeLabFTW APIから疎結合にしておくことで、
単体テスト・別データソースへの差し替えが容易になる。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Party:
    """creator / owner / vendor / instrument (特定グローバル要素) 共通表現"""
    key: str          # named_uuid() の材料になる安定キー (例: "elabftw-user-42")
    name: str         # <name> に入れる表示名 (QName想定、呼び出し側でns付与)
    description: Optional[str] = None


@dataclass
class PropertyValue:
    """<property> 1要素分。xsi_typeはmaiml_xml.pyの型名 (例: "stringType")"""
    key: str                       # 名前空間プレフィックス付きQName (例: "ns1:SampleName")
    xsi_type: str
    value: Optional[str] = None
    values: Optional[list] = None  # List系型の場合
    description: Optional[str] = None
    format_string: Optional[str] = None
    units: Optional[str] = None
    scale_factor: Optional[str] = None


@dataclass
class FileRef:
    """eLabFTWの添付ファイル -> <insertion> 用"""
    filename: str
    uri: str            # 相対URI or eLabFTWのダウンロードURL
    hash_b64: str
    hash_method: str = "SHA-256"


@dataclass
class LinkedItem:
    """eLabFTWでリンクされたデータベースアイテム(試料・機器等) -> <material>"""
    elab_id: int
    title: str
    category: Optional[str] = None
    properties: list = field(default_factory=list)   # list[PropertyValue]


@dataclass
class Step:
    """eLabFTWのプロトコルステップ (Steps API) -> pnml/transition + program/instruction"""
    elab_id: int
    title: str
    body: Optional[str] = None
    started_at: Optional[datetime] = None      # finish_time等から取得できれば
    finished_at: Optional[datetime] = None
    is_finished: bool = False


@dataclass
class ExperimentData:
    """1つのeLabFTW実験(Experiment)の変換対象データ一式"""
    elab_id: int
    title: str
    date: datetime
    body_text: Optional[str] = None          # 実験本文 (HTML除去済み推奨)
    owner: Optional[Party] = None            # 実験の所有者/実施者
    creator: Optional[Party] = None          # 計測装置/ソフトウェア (カスタムフィールド由来。無指定ならツール自身にフォールバック)
    vendor: Optional[Party] = None           # creatorの製造元 (カスタムフィールド由来。無指定ならDeltablotにフォールバック)
    instruments: list = field(default_factory=list)    # list[Party] 装置の一般名/型式 (複数可。任意)
    steps: list = field(default_factory=list)          # list[Step]
    materials: list = field(default_factory=list)      # list[LinkedItem]
    condition_properties: list = field(default_factory=list)  # list[PropertyValue] (カスタムフィールド由来)
    result_properties: list = field(default_factory=list)     # list[PropertyValue] (本文などから)
    uploads: list = field(default_factory=list)         # list[FileRef]
    elab_url: Optional[str] = None           # 実験のパーマリンク (insertion/uriに使用)
