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

    def _extra_fields_to_properties(self, metadata, ns_prefix: str) -> list:
        props = []
        if metadata is None or not getattr(metadata, "extra_fields", None):
            return props
        for field_name, field in metadata.extra_fields.items():
            xsi_type = _EXTRA_FIELD_TYPE_MAP.get(field.type, "stringType")
            key = f"{ns_prefix}:{_sanitize_ncname(field_name)}"
            props.append(PropertyValue(
                key=key,
                xsi_type=xsi_type,
                value=field.value,
                description=getattr(field, "description", None) or None,
            ))
        return props

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

    def fetch_experiment(self, experiment_id: int, ns_prefix: str = "ns1") -> ExperimentData:
        experiment = self.experiments_api.get_experiment(experiment_id)

        owner = self._owner_party(experiment.userid, experiment.fullname)
        steps = self._steps_to_model(experiment)
        materials = self._fetch_linked_materials(experiment, ns_prefix)
        condition_props = self._extra_fields_to_properties(experiment.metadata, ns_prefix)
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
