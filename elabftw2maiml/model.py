"""
eLabFTW から取得したデータを MaiML ビルダーに渡すための中間データモデル。

elabftw_client.py がこれらのdataclassを組み立て、maiml_builder.py が
これらを使ってXMLを構築する。ビルダー自体をeLabFTW APIから疎結合にしておくことで、
単体テスト・別データソースへの差し替えが容易になる。

データモデル対称化改修 (elabftw2MaiML_model_refactoring_plan.md) により、
MaiMLの material/condition/result の構造的な対称性に合わせて、
MaterialData/ConditionData/ResultData をいずれも「オブジェクト + properties」
という共通の形で表現している (旧: materialのみLinkedItemというオブジェクト単位、
condition/resultはPropertyValueのフラットなリストという非対称な構造だった)。
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
class MaterialData:
    """MaiMLの materialTemplate/material に対応する「データオブジェクト + properties」。

    eLabFTWでリンクされたデータベースアイテム (試料・機器等)、実験自身のMATERIAL
    グループの自己カスタムフィールドをまとめた合成アイテム、interpretation
    (--field-mapping) 経由のmaterial候補をまとめたコンテナ、のいずれもこの型で
    表現する。旧 `LinkedItem` を置き換える (elabftw2MaiML_model_refactoring_plan.md
    5節)。
    """
    key: str
    """内部識別子。同じkeyのMaterialDataは同一アイテムとして扱われ、properties が
    1つにまとめられる (例: "material:item:123"=リンクされたeLabFTW Item#123、
    "material:experiment:456"=実験#456自身を表す合成アイテム)。"""
    title: str
    properties: list = field(default_factory=list)   # list[PropertyValue]
    elab_id: Optional[int] = None
    """eLabFTW側の実体アイテムID。実体を持たない (=interpretation由来のみで
    構成された) アイテムは None。`0` は「実験自身を表す合成アイテム」を示す
    従来からの規約 (elabftw_client.py の実験自身のMATERIALグループ由来アイテムと、
    interpretation.apply の実験全体向けcontextの候補は、共に elab_id=0・
    同一keyを使うことで自動的に1つのアイテムへ統合される)。"""
    category: Optional[str] = None
    step_id: Optional[int] = None
    """将来のStep単位Material対応のためのメタデータ (elabftw2MaiML_model_
    refactoring_plan.md 14節)。現在のMaimlBuilderはこの値をplaceRef/arcの決定には
    使わず、全てのMaterialDataを常に最初のSTEPが消費する共有placeへ接続する。"""


@dataclass
class ConditionData:
    """MaiMLの conditionTemplate/condition に対応するコンテナ。MaterialData/
    ResultDataと同じ「オブジェクト + properties」の形に揃えている
    (旧: 実験全体で1つの `PropertyValue` のフラットなリストのみだった)。"""
    key: str
    title: Optional[str] = None
    properties: list = field(default_factory=list)   # list[PropertyValue]
    step_id: Optional[int] = None
    """将来のStep単位Condition対応のためのメタデータ。現在のMaimlBuilderは
    全てのConditionDataを、最初のSTEPが消費する共有placeへ接続する
    (Step単位のPNML経路の構築自体は今回のスコープ外。改修方針14節参照)。"""


@dataclass
class ResultData:
    """MaiMLの resultTemplate/result に対応するコンテナ。"""
    key: str
    title: Optional[str] = None
    properties: list = field(default_factory=list)   # list[PropertyValue]
    step_id: Optional[int] = None
    """このResultDataをどのSTEPのresultTemplate/resultインスタンスに反映するかを
    eLabFTW Step の `elab_id` で指定する。Noneの場合は最後のSTEPに反映する
    (Step概念が無かった頃からの既定の挙動を維持する既定値)。指定した`elab_id`に
    一致するSTEPが無い場合もMaimlBuilderは最後のSTEPにフォールバックする。"""
    uploads: list = field(default_factory=list)       # list[FileRef]
    """このResultDataに属する添付ファイル (<insertion>として反映)。"""


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
    materials: list = field(default_factory=list)      # list[MaterialData]
    conditions: list = field(default_factory=list)     # list[ConditionData]
    results: list = field(default_factory=list)        # list[ResultData]
    elab_url: Optional[str] = None           # 実験のパーマリンク (insertion/uriに使用)
