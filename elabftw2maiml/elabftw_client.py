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
        if metadata is None or not getattr(metadata, "extra_fields", None):
            return props
        exclude_names = exclude_names or set()
        for field_name, field in metadata.extra_fields.items():
            if field_name in exclude_names:
                continue
            xsi_type = _EXTRA_FIELD_TYPE_MAP.get(field.type, "stringType")
            key = f"{ns_prefix}:{_sanitize_ncname(field_name)}"
            props.append(PropertyValue(
                key=key,
                xsi_type=xsi_type,
                value=field.value,
                description=getattr(field, "description", None) or None,
            ))
        return props

    def _find_field(self, metadata, candidates: list) -> tuple:
        """
        候補名リストのいずれかに(大文字小文字を区別せず)一致し、かつ値が入力されている
        カスタムフィールドを探す。見つかれば (実際のフィールド名, 値) を返す。
        """
        if metadata is None or not getattr(metadata, "extra_fields", None):
            return None, None
        lower_map = {k.lower(): k for k in metadata.extra_fields.keys()}
        for cand in candidates:
            actual_key = lower_map.get(cand.lower())
            if actual_key:
                f = metadata.extra_fields[actual_key]
                if f.value:
                    return actual_key, f.value
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

    def _fetch_linked_materials(self, experiment, ns_prefix: str) -> list:
        materials = []
        for link in (experiment.items_links or []):
            item_props = []
            try:
                item = self.items_api.get_item(link.entityid)
                item_props = self._extra_fields_to_properties(item.metadata, ns_prefix)
            except ApiException:
                # アイテム詳細が取れなくてもタイトルだけで材料インスタンスを作る
                pass
            materials.append(LinkedItem(
                elab_id=link.entityid,
                title=link.title,
                category=getattr(link, "category_title", None),
                properties=item_props,
            ))
        return materials

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
                          vendor_field_candidates: Optional[list] = None) -> ExperimentData:
        """
        creator_field_candidates / vendor_field_candidates:
            「使用装置」「装置メーカー」等、実験のカスタムフィールドからcreator/vendorを
            拾い上げる際に探すフィールド名の候補リスト (大文字小文字を区別せずマッチ)。
            省略時は DEFAULT_CREATOR_FIELD_CANDIDATES / DEFAULT_VENDOR_FIELD_CANDIDATES を使う。
            該当フィールドが無い/未入力の場合は、creator=このツール自身 / vendor=Deltablot に
            フォールバックする (builder.py 側の既定動作)。
        """
        experiment = self.experiments_api.get_experiment(experiment_id)

        owner = self._owner_party(experiment.userid, experiment.fullname)
        steps = self._steps_to_model(experiment)
        materials = self._fetch_linked_materials(experiment, ns_prefix)

        creator_party, vendor_party, used_fields = self._creator_vendor_parties(
            experiment.metadata,
            creator_field_candidates or DEFAULT_CREATOR_FIELD_CANDIDATES,
            vendor_field_candidates or DEFAULT_VENDOR_FIELD_CANDIDATES,
        )
        instrument_party = None
        if creator_party is not None:
            # 一般名(instrument)と個体(creator)を同じ表示名から作る簡易実装。
            # 型式とシリアル番号を別フィールドで分けて管理したい場合は、
            # creator_party/instrument_partyの生成ロジックをここで分離してください。
            instrument_party = Party(key=f"elabftw-instrument:{creator_party.name}", name=creator_party.name)

        condition_props = self._extra_fields_to_properties(
            experiment.metadata, ns_prefix, exclude_names=used_fields)

        uploads = self._fetch_uploads(experiment)
        body_text = _strip_html(experiment.body)

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
