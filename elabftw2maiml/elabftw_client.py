"""
elabapi-python を使って eLabFTW から実験データを取得し、
model.ExperimentData に変換する。

前提: 実験データの「入力」自体は eLabFTW の GUI (ブラウザ / elabftw/desktop) で行い、
本モジュールは完成した実験データを読み出すだけ (書き込みは行わない)。

使い方:
    client = ElabftwClient(host_url="https://elab.example.org/api/v2",
                            api_key="xxxxxxxxxxxx")
    exp_data = client.fetch_experiment(123, ns_prefix="ns1")
"""
from __future__ import annotations

import html as html_module
import json
import re
from datetime import datetime
from typing import Optional

import elabapi_python
from elabapi_python.rest import ApiException

from .model import ExperimentData, Party, PropertyValue, LinkedItem, Step, FileRef

# eLabFTWのカスタムフィールド type -> MaiMLのxsi:type マッピング。
# 未知のtypeは stringType にフォールバックする。
_EXTRA_FIELD_TYPE_MAP = {
    "number": "doubleType",
    "date": "dateTimeType",
    "datetime-local": "dateTimeType",
    "checkbox": "booleanType",
    "email": "stringType",
    "url": "uriType",
    "text": "stringType",
    "textarea": "stringType",
    "radio": "stringType",
    "select": "stringType",
    "items": "stringType",
    "users": "stringType",
    "experiments": "stringType",
}

# 実験のカスタムフィールド名から creator(使用装置) / vendor(装置メーカー) を
# 拾い上げる際の既定候補名 (大文字小文字を区別せずマッチ)。
# 実験ごとにフィールド名が異なる場合は fetch_experiment() の引数で上書き可能。
DEFAULT_CREATOR_FIELD_CANDIDATES = ["使用装置", "使用機器", "装置", "機器", "Instrument", "Equipment", "Device"]
DEFAULT_VENDOR_FIELD_CANDIDATES = ["装置メーカー", "メーカー", "製造元", "Vendor", "Manufacturer"]

# リンクされたアイテム (items_links) を、カテゴリ名・タグの文字列から
# material/condition/result/creator/instrument/vendor のいずれの役割として扱うか判定する際の
# 既定候補文字列 (大文字小文字を区別せず部分一致)。
# 優先順位は _ROLE_PRIORITY の順 (先に一致した役割が採用される)。一致しなければ "material"。
DEFAULT_ROLE_CATEGORY_CANDIDATES = {
    "creator": ["Creator", "作成者", "使用装置", "使用機器"],
    "vendor": ["Vendor", "メーカー", "製造元", "Manufacturer"],
    "condition": ["Conditions", "Condition", "条件"],
    "result": ["Results", "Result", "結果"],
    "instrument": ["Resources", "Resource", "Equipment", "装置", "機器", "Instrument"],
    "material": ["Consumables", "Samples", "Sample", "試料", "材料", "Material"],
}
DEFAULT_ROLE_TAG_CANDIDATES = {k: list(v) for k, v in DEFAULT_ROLE_CATEGORY_CANDIDATES.items()}
# 一致判定を試みる順序 (material以外を先に判定し、どれにも当てはまらなければmaterial扱いにする)
_ROLE_PRIORITY = ["creator", "vendor", "condition", "result", "instrument", "material"]

# eLabFTWの「カスタムフィールドのグループ化」機能 (CUSTOM FIELDS > MATERIAL/CONDITION/... の
# ように折りたたみグループを作れる機能) で使われるグループ名から、
# material/condition/result のどの役割として扱うかを判定する際の既定候補
# (大文字小文字を区別せず部分一致)。これはリンクされたアイテムのカテゴリ/タグ判定とは別の仕組みで、
# 実験"自身"のカスタムフィールドに対して適用される。どれにも一致しないグループ (グループ無し含む)
# のフィールドは既定で "condition" として扱う (従来の挙動と互換)。
DEFAULT_FIELD_GROUP_CANDIDATES = {
    "material": ["MATERIAL", "材料", "試料", "Sample"],
    "result": ["RESULT", "RESULTS", "結果"],
    # "condition" は明示候補を指定しなくても、どれにも一致しない場合のフォールバック先になる
}
_FIELD_GROUP_ROLE_PRIORITY = ["material", "result"]  # 先に一致した方が採用され、どちらにも該当しなければ"condition"

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: Optional[str]) -> Optional[str]:
    """実験本文のHTMLを簡易的にプレーンテキスト化する。"""
    if not html:
        return None
    text = _TAG_RE.sub(" ", html)
    text = html_module.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    # eLabFTWは "YYYY-MM-DD HH:MM:SS" 形式を返すことが多い
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _normalize_extra_fields(metadata) -> dict:
    """
    eLabFTWの `metadata` は DB上はJSON文字列で保持されており、APIレスポンスでも
    JSON文字列のまま返ってくることがある。`elabapi_python` はこれを自動的に
    Metadataオブジェクトへ変換できない場合があり、その場合 `metadata.extra_fields`
    へのアクセスがNoneになってしまう (=カスタムフィールド情報が一切拾えないバグの原因)。

    この関数は、metadataが
      - elabapi_python.Metadata オブジェクト (正しくパースされている場合)
      - dict (JSONが辞書として渡ってくる場合)
      - str (未パースのJSON文字列の場合)
      - None
    のいずれであっても、{field_name: {"type":..,"value":..,"description":..,"group_id":..}} という
    通常のdictに正規化して返す。
    """
    if metadata is None:
        return {}

    # 1) 既に Metadata オブジェクトとして正しくパースされている場合
    extra = getattr(metadata, "extra_fields", None)
    if extra:
        out = {}
        for k, v in extra.items():
            out[k] = {
                "type": getattr(v, "type", None) if not isinstance(v, dict) else v.get("type"),
                "value": getattr(v, "value", None) if not isinstance(v, dict) else v.get("value"),
                "description": getattr(v, "description", None) if not isinstance(v, dict) else v.get("description"),
                "group_id": getattr(v, "group_id", None) if not isinstance(v, dict) else v.get("group_id"),
            }
        return out

    # 2) JSON文字列、またはパース済みdictの場合
    raw = metadata
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    if isinstance(raw, dict):
        return raw.get("extra_fields") or {}

    return {}


def _normalize_field_groups(metadata) -> dict:
    """
    eLabFTWの「カスタムフィールドのグループ化」機能で定義されたグループ一覧
    (`metadata.elabftw.extra_fields_groups`、実体は [{"id":1,"name":"MATERIAL"}, ...]) を
    {group_id: group_name} の辞書に正規化する。metadataの型ゆれ (オブジェクト/dict/JSON文字列/None)
    は _normalize_extra_fields と同様に吸収する。
    """
    if metadata is None:
        return {}

    # 1) 既に Metadata オブジェクトとして正しくパースされている場合
    elabftw_obj = getattr(metadata, "elabftw", None)
    if elabftw_obj is not None:
        groups = getattr(elabftw_obj, "extra_fields_groups", None) or []
        out = {}
        for g in groups:
            gid = getattr(g, "id", None) if not isinstance(g, dict) else g.get("id")
            name = getattr(g, "name", None) if not isinstance(g, dict) else g.get("name")
            if gid is not None and name:
                out[gid] = name
        if out:
            return out

    # 2) JSON文字列、またはパース済みdictの場合
    raw = metadata
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    if isinstance(raw, dict):
        groups = (raw.get("elabftw") or {}).get("extra_fields_groups") or []
        return {g.get("id"): g.get("name") for g in groups if g.get("id") is not None and g.get("name")}

    return {}


def _classify_field_group_role(group_name: Optional[str], field_group_candidates: dict) -> str:
    """
    グループ名の文字列から material/result のどちらかに該当するか判定する。
    大文字小文字を区別せず部分一致。どちらにも該当しない (グループ無し含む) 場合は "condition"。
    """
    if group_name:
        low = group_name.lower()
        for role in _FIELD_GROUP_ROLE_PRIORITY:
            candidates = field_group_candidates.get(role, [])
            if any(c.lower() in low for c in candidates):
                return role
    return "condition"


class _ExtraField:
    """_normalize_extra_fields() が返す辞書の値 (dict) を、既存コードの
    `field.type` / `field.value` / `field.description` という属性アクセスの
    ままでも扱えるようにする薄いラッパー。"""

    def __init__(self, d: dict):
        self.type = d.get("type")
        self.value = d.get("value")
        self.description = d.get("description")
        self.group_id = d.get("group_id")


class ElabftwClient:
    def __init__(self, host_url: str, api_key: str, verify_ssl: bool = True):
        """
        host_url: 例 "https://elab.example.org/api/v2"
                  (elabftw/desktop でローカル起動している場合は
                   "https://localhost:PORT/api/v2" 等になる)
        api_key:  eLabFTWのユーザー設定画面で発行するAPIキー
        """
        config = elabapi_python.Configuration()
        config.host = host_url
        config.api_key["Authorization"] = api_key
        config.debug = False
        config.verify_ssl = verify_ssl
        self.host_url = host_url.rstrip("/")
        self.base_url = re.sub(r"/api/v2/?$", "", self.host_url)

        api_client = elabapi_python.ApiClient(config)
        api_client.set_default_header(header_name="Authorization", header_value=api_key)
        self.api_client = api_client

        self.experiments_api = elabapi_python.ExperimentsApi(api_client)
        self.items_api = elabapi_python.ItemsApi(api_client)
        self.uploads_api = elabapi_python.UploadsApi(api_client)
        self.users_api = elabapi_python.UsersApi(api_client)

    # -- 個別要素の変換 ----------------------------------------------------

    def _owner_party(self, userid: int, fullname: Optional[str]) -> Party:
        display = fullname or f"user#{userid}"
        return Party(
            key=f"elabftw-user-{userid}@{self.base_url}",
            name=display,
        )

    def _extra_fields_to_properties(self, metadata, ns_prefix: str, exclude_names: Optional[set] = None) -> list:
        props = []
        extra_fields = _normalize_extra_fields(metadata)
        if not extra_fields:
            return props
        exclude_names = exclude_names or set()
        for field_name, field_dict in extra_fields.items():
            if field_name in exclude_names:
                continue
            field = _ExtraField(field_dict)
            xsi_type = _EXTRA_FIELD_TYPE_MAP.get(field.type, "stringType")
            key = f"{ns_prefix}:{_sanitize_ncname(field_name)}"
            props.append(PropertyValue(
                key=key,
                xsi_type=xsi_type,
                value=field.value,
                description=field.description or None,
            ))
        return props

    def _split_extra_fields_by_group(self, metadata, ns_prefix: str,
                                      exclude_names: Optional[set] = None,
                                      field_group_candidates: Optional[dict] = None) -> dict:
        """
        実験"自身"のカスタムフィールドを、eLabFTWの「グループ化」機能 (CUSTOM FIELDS の中の
        MATERIAL/CONDITION/RESULT のような折りたたみグループ) のグループ名から
        material/condition/result に振り分ける。

        戻り値: {"material": [PropertyValue, ...], "condition": [...], "result": [...]}

        グループ名がどの候補にも一致しない場合 (グループ無しのフィールドも含む) は
        "condition" として扱う (従来の挙動と互換)。
        """
        candidates = dict(DEFAULT_FIELD_GROUP_CANDIDATES)
        if field_group_candidates:
            candidates.update(field_group_candidates)
        exclude_names = exclude_names or set()
        result = {"material": [], "condition": [], "result": []}

        extra_fields = _normalize_extra_fields(metadata)
        if not extra_fields:
            return result
        group_names = _normalize_field_groups(metadata)

        for field_name, field_dict in extra_fields.items():
            if field_name in exclude_names:
                continue
            field = _ExtraField(field_dict)
            group_name = group_names.get(field.group_id) if field.group_id is not None else None
            role = _classify_field_group_role(group_name, candidates)

            xsi_type = _EXTRA_FIELD_TYPE_MAP.get(field.type, "stringType")
            key = f"{ns_prefix}:{_sanitize_ncname(field_name)}"
            result[role].append(PropertyValue(
                key=key,
                xsi_type=xsi_type,
                value=field.value,
                description=field.description or None,
            ))
        return result

    def _find_field(self, metadata, candidates: list) -> tuple:
        """
        候補名リストのいずれかに(大文字小文字を区別せず)一致し、かつ値が入力されている
        カスタムフィールドを探す。見つかれば (実際のフィールド名, 値) を返す。
        """
        extra_fields = _normalize_extra_fields(metadata)
        if not extra_fields:
            return None, None
        lower_map = {k.lower(): k for k in extra_fields.keys()}
        for cand in candidates:
            actual_key = lower_map.get(cand.lower())
            if actual_key:
                value = extra_fields[actual_key].get("value")
                if value:
                    return actual_key, value
        return None, None

    def _creator_vendor_parties(self, metadata, creator_candidates: list,
                                 vendor_candidates: list) -> tuple:
        """
        「使用装置」等のカスタムフィールドから creator/vendor の Party を作る。
        戻り値: (creator_party or None, vendor_party or None, {使用したフィールド名の集合})

        - creator候補フィールドが見つからなければ (None, None, set()) を返す
          (呼び出し側でツール自身/Deltablotへのフォールバックが働く)
        - creatorは見つかったがvendor候補が見つからない場合、creatorTypeがvendorRefを
          1つ以上要求するため「メーカー不明」のダミーvendorを作る
        """
        creator_field, creator_value = self._find_field(metadata, creator_candidates)
        if not creator_value:
            return None, None, set()

        used_fields = {creator_field}
        creator_party = Party(key=f"elabftw-device:{creator_value.strip()}", name=creator_value.strip())

        vendor_field, vendor_value = self._find_field(metadata, vendor_candidates)
        if vendor_value:
            used_fields.add(vendor_field)
            vendor_party = Party(key=f"elabftw-device-vendor:{vendor_value.strip()}", name=vendor_value.strip())
        else:
            vendor_party = Party(
                key=f"elabftw-device-vendor-unknown:{creator_value.strip()}",
                name=f"(unspecified vendor of {creator_value.strip()})",
            )
        return creator_party, vendor_party, used_fields

    def _get_raw_json(self, resource_path: str) -> Optional[dict]:
        """
        swagger-codegen (elabapi-python) の自動デシリアライズを経由せず、
        生のJSONレスポンスをそのまま取得する。

        `elabapi_python.ApiClient.__deserialize_model` は、値が dict/list でない場合
        (=metadataがJSON文字列のまま返ってきた場合) その値を静かに捨てて空のオブジェクトを
        作ってしまうため、SDK経由で取得した `experiment.metadata` / `item.metadata` は
        実質的に空になってしまう。metadataだけはこの生JSON取得で確実に読み取る。
        """
        try:
            response = self.api_client.call_api(
                resource_path, "GET",
                header_params={"Accept": "application/json"},
                auth_settings=["token"],
                _preload_content=False,
                _return_http_data_only=True,
            )
            return json.loads(response.data)
        except ApiException as e:
            print(f"  [警告] {resource_path} の取得に失敗しました: {e}")
            return None
        except (ValueError, TypeError) as e:
            print(f"  [警告] {resource_path} のJSON解析に失敗しました: {e}")
            return None

    def _classify_role(self, category_title: Optional[str], tags: Optional[str],
                        category_candidates: dict, tag_candidates: dict) -> str:
        """
        リンクされたアイテムのカテゴリ名・タグ文字列から、material/condition/result/
        creator/instrument/vendor のどの役割として扱うかを判定する。
        大文字小文字を区別せず部分一致で判定し、_ROLE_PRIORITY の順に調べる。
        どれにも該当しなければ "material" とみなす (従来の既定動作と互換)。
        """
        haystacks = []
        if category_title:
            haystacks.append(category_title.lower())
        if tags:
            haystacks.extend(t.strip().lower() for t in re.split(r"[,|]", tags) if t.strip())

        for role in _ROLE_PRIORITY:
            if role == "material":
                continue  # materialは最後にフォールバックとして扱う
            cat_cands = category_candidates.get(role, [])
            tag_cands = tag_candidates.get(role, [])
            for h in haystacks:
                if any(c.lower() in h for c in cat_cands) or any(c.lower() in h for c in tag_cands):
                    return role
        return "material"

    def _fetch_linked_items(self, experiment, ns_prefix: str,
                             role_category_candidates: Optional[dict] = None,
                             role_tag_candidates: Optional[dict] = None) -> dict:
        """
        戻り値: {"material": [LinkedItem, ...],
                 "condition": [(link, raw_item, props), ...],
                 "result": [(link, raw_item, props), ...],
                 "creator": [(link, raw_item), ...],
                 "instrument": [(link, raw_item), ...],
                 "vendor": [(link, raw_item), ...]}

        リンクされたアイテム (items_links) 1件ごとに、カテゴリ名/タグから役割を判定し、
        対応するバケツに振り分ける。material以外は、呼び出し側 (fetch_experiment) で
        condition/result/creator/vendor/instrumentの情報源として使う。
        """
        category_candidates = dict(DEFAULT_ROLE_CATEGORY_CANDIDATES)
        if role_category_candidates:
            category_candidates.update(role_category_candidates)
        tag_candidates = dict(DEFAULT_ROLE_TAG_CANDIDATES)
        if role_tag_candidates:
            tag_candidates.update(role_tag_candidates)

        buckets = {"material": [], "condition": [], "result": [], "creator": [], "instrument": [], "vendor": []}
        links = experiment.items_links or []
        if not links:
            print("  [警告] 実験にリンクされたアイテム (items_links) が見つかりません。"
                  "eLabFTW側で試料・機器等をリンクしていない場合は正常です。")
        for link in links:
            raw_item = self._get_raw_json(f"/items/{link.entityid}")
            item_props = []
            raw_tags = None
            if raw_item is not None:
                item_props = self._extra_fields_to_properties(raw_item.get("metadata"), ns_prefix)
                raw_tags = raw_item.get("tags")

            category_title = getattr(link, "category_title", None)
            role = self._classify_role(category_title, raw_tags, category_candidates, tag_candidates)
            print(f"  [情報] リンクされたアイテム #{link.entityid} ({link.title}) を"
                  f"'{role}' として扱います (category={category_title!r}, tags={raw_tags!r})")

            if role == "material":
                if raw_item is not None and not item_props:
                    print(f"  [情報] リンクされたアイテム #{link.entityid} ({link.title}) に"
                          f"カスタムフィールドが見つかりませんでした。")
                buckets["material"].append(LinkedItem(
                    elab_id=link.entityid,
                    title=link.title,
                    category=category_title,
                    properties=item_props,
                ))
            elif role in ("condition", "result"):
                buckets[role].append((link, raw_item, item_props))
            else:  # creator / vendor / instrument
                buckets[role].append((link, raw_item))
        return buckets

    def _fetch_uploads(self, experiment) -> list:
        files = []
        for u in (experiment.uploads or []):
            if not u.hash:
                continue
            download_url = f"{self.base_url}/app/download.php?f={u.long_name}&name={u.real_name}"
            files.append(FileRef(
                filename=u.real_name,
                uri=download_url,
                hash_b64=u.hash,
                hash_method=(u.hash_algorithm or "sha256").upper(),
            ))
        return files

    def _steps_to_model(self, experiment) -> list:
        steps = []
        for s in sorted(experiment.steps or [], key=lambda x: x.ordering or 0):
            steps.append(Step(
                elab_id=s.id,
                title=_strip_html(s.body) or f"step-{s.id}",
                body=_strip_html(s.body),
                finished_at=_parse_dt(s.finished_time) if s.finished else None,
                is_finished=bool(s.finished),
            ))
        return steps

    # -- 公開API -------------------------------------------------------------

    def fetch_experiment(self, experiment_id: int, ns_prefix: str = "ns1",
                          creator_field_candidates: Optional[list] = None,
                          vendor_field_candidates: Optional[list] = None,
                          role_category_candidates: Optional[dict] = None,
                          role_tag_candidates: Optional[dict] = None,
                          field_group_candidates: Optional[dict] = None) -> ExperimentData:
        """
        creator_field_candidates / vendor_field_candidates:
            「使用装置」「装置メーカー」等、実験のカスタムフィールドからcreator/vendorを
            拾い上げる際に探すフィールド名の候補リスト (大文字小文字を区別せずマッチ)。
            省略時は DEFAULT_CREATOR_FIELD_CANDIDATES / DEFAULT_VENDOR_FIELD_CANDIDATES を使う。
        role_category_candidates / role_tag_candidates:
            リンクされたアイテムを material/condition/result/creator/instrument/vendor の
            どの役割として扱うかを、カテゴリ名・タグの文字列から判定するための候補辞書
            ({role: [候補文字列, ...]})。省略時は DEFAULT_ROLE_CATEGORY_CANDIDATES /
            DEFAULT_ROLE_TAG_CANDIDATES を使う。
        field_group_candidates:
            実験"自身"のカスタムフィールドを、eLabFTWの「フィールドグループ」機能
            (CUSTOM FIELDS の中の MATERIAL/CONDITION/RESULT のような折りたたみグループ) の
            グループ名から material/condition/result のどれに振り分けるかの候補辞書
            ({role: [候補文字列, ...]}, role は "material"/"result" のみ指定可。
            "condition" はどれにも一致しない場合のフォールバック先として自動的に使われる)。
            省略時は DEFAULT_FIELD_GROUP_CANDIDATES を使う。

        creator/vendor/instrumentの決定優先順位:
            1. カテゴリ/タグから明示的に creator/vendor/instrument と判定されたリンクアイテム
            2. 実験のカスタムフィールド (--creator-field/--vendor-field) (creator/vendorのみ)
            3. カテゴリ/タグから "instrument" と判定されたリンクアイテムをcreatorのフォールバックに使う
               (そのアイテム自身のカスタムフィールドからvendorも探す)
            4. どれも無い場合、このツール自身 / Deltablot にフォールバック (builder.py側)

        condition/resultの決定:
            実験のExtra Fields (condition) / 実験本文・タグ (result) に加え、
            カテゴリ/タグから "condition"/"result" と判定されたリンクアイテムの
            カスタムフィールドも、それぞれconditionTemplate/resultTemplateにマージする。
        """
        experiment = self.experiments_api.get_experiment(experiment_id)
        raw_experiment = self._get_raw_json(f"/experiments/{experiment_id}")
        raw_metadata = raw_experiment.get("metadata") if raw_experiment else None

        creator_field_candidates = creator_field_candidates or DEFAULT_CREATOR_FIELD_CANDIDATES
        vendor_field_candidates = vendor_field_candidates or DEFAULT_VENDOR_FIELD_CANDIDATES

        owner = self._owner_party(experiment.userid, experiment.fullname)
        steps = self._steps_to_model(experiment)
        buckets = self._fetch_linked_items(
            experiment, ns_prefix, role_category_candidates, role_tag_candidates)
        materials = buckets["material"]

        # -- creator/vendor: 1) 明示的にタグ/カテゴリ付けされたリンクアイテムを最優先 --------
        creator_party = None
        vendor_party = None
        if buckets["creator"]:
            link, _raw = buckets["creator"][0]
            creator_party = Party(key=f"elabftw-device:{link.title}", name=link.title)
        if buckets["vendor"]:
            link, _raw = buckets["vendor"][0]
            vendor_party = Party(key=f"elabftw-device-vendor:{link.title}", name=link.title)

        # -- 2) 実験のカスタムフィールド (まだ決まっていない方のみ採用) --------------------
        cf_creator_party, cf_vendor_party, used_fields = self._creator_vendor_parties(
            raw_metadata, creator_field_candidates, vendor_field_candidates)
        if creator_party is None:
            creator_party = cf_creator_party
        if vendor_party is None:
            vendor_party = cf_vendor_party

        # -- 3) "instrument" と判定されたリンクアイテムをcreatorのフォールバックに使う -----
        if creator_party is None and buckets["instrument"]:
            link, raw_item = buckets["instrument"][0]
            creator_party = Party(key=f"elabftw-device:{link.title}", name=link.title)
            if vendor_party is None:
                item_metadata = raw_item.get("metadata") if raw_item else None
                _, vendor_value = self._find_field(item_metadata, vendor_field_candidates)
                if vendor_value:
                    vendor_party = Party(key=f"elabftw-device-vendor:{vendor_value.strip()}",
                                          name=vendor_value.strip())

        if creator_party is not None and vendor_party is None:
            vendor_party = Party(
                key=f"elabftw-device-vendor-unknown:{creator_party.name}",
                name=f"(unspecified vendor of {creator_party.name})",
            )

        # -- instrument: "instrument" と判定されたリンクアイテムを優先、無ければcreator名を流用 --
        instrument_party = None
        if buckets["instrument"]:
            link, _raw = buckets["instrument"][0]
            instrument_party = Party(key=f"elabftw-instrument:{link.title}", name=link.title)
        elif creator_party is not None:
            instrument_party = Party(key=f"elabftw-instrument:{creator_party.name}", name=creator_party.name)

        # -- 実験"自身"のカスタムフィールドを、eLabFTWのグループ化機能 (MATERIAL/CONDITION/RESULT
        #    のような折りたたみグループ) のグループ名から material/condition/result に振り分ける。
        #    グループ無し、またはどの候補にも一致しないグループのフィールドは "condition" 扱い。
        own_fields = self._split_extra_fields_by_group(
            raw_metadata, ns_prefix, exclude_names=used_fields,
            field_group_candidates=field_group_candidates,
        )

        # -- material: リンクアイテム由来のmaterialsに加え、MATERIALグループの自己カスタムフィールド
        #    があれば、実験自身を表す合成LinkedItemとしてmaterialsに追加する ---------------------
        if own_fields["material"]:
            materials = list(materials) + [LinkedItem(
                elab_id=0,
                title=experiment.title,
                category="(experiment own MATERIAL fields)",
                properties=own_fields["material"],
            )]

        # -- condition: 実験のExtra Fields (CONDITIONグループ/グループ無し)
        #    + "condition"と判定されたリンクアイテム ------------------------------------------
        condition_props = list(own_fields["condition"])
        for link, raw_item, item_props in buckets["condition"]:
            if not item_props:
                print(f"  [情報] 条件として扱われたリンクアイテム #{link.entityid} ({link.title}) に"
                      f"カスタムフィールドが見つかりませんでした。")
            condition_props.extend(item_props)
        if not condition_props:
            print("  [情報] 実験のカスタムフィールド (Extra Fields) が見つかりませんでした。"
                  "eLabFTW側でカスタムフィールドを設定していない場合は正常です。")

        uploads = self._fetch_uploads(experiment)
        body_text = _strip_html(experiment.body)

        # -- result: 実験本文・タグ + RESULTグループの自己カスタムフィールド
        #    + "result"と判定されたリンクアイテム --------------------------------------------
        result_props = []
        if body_text:
            result_props.append(PropertyValue(
                key=f"{ns_prefix}:experimentBody",
                xsi_type="stringType",
                value=body_text,
            ))
        if experiment.tags:
            result_props.append(PropertyValue(
                key=f"{ns_prefix}:tags",
                xsi_type="stringType",
                value=experiment.tags,
            ))
        result_props.extend(own_fields["result"])
        for link, raw_item, item_props in buckets["result"]:
            result_props.append(PropertyValue(
                key=f"{ns_prefix}:resultItem",
                xsi_type="stringType",
                value=link.title,
            ))
            result_props.extend(item_props)

        exp_date = _parse_dt(getattr(experiment, "_date", None) or experiment.created_at) \
            or datetime.utcnow()

        return ExperimentData(
            elab_id=experiment.id,
            title=experiment.title,
            date=exp_date,
            body_text=body_text,
            owner=owner,
            creator=creator_party,
            vendor=vendor_party,
            instrument=instrument_party,
            steps=steps,
            materials=materials,
            condition_properties=condition_props,
            result_properties=result_props,
            uploads=uploads,
            elab_url=f"{self.base_url}/experiments.php?mode=view&id={experiment.id}",
        )


def _sanitize_ncname(name: str) -> str:
    """カスタムフィールド名をQNameのローカル部として使える形に変換する。"""
    cleaned = re.sub(r"[^0-9A-Za-z_一-龠ぁ-んァ-ヶー]", "_", name.strip())
    if cleaned and cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned or "field"
