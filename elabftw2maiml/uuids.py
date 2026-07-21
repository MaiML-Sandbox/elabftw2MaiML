"""
UUID generation helpers.

MaiML AI共通指示書 4.1節のルール:
  - 通常のグローバル要素 (document/protocol/data/method/program 等)
        -> RFC4122 v4 (乱数)。ファイル生成のたびに新規発行。
  - 特定グローバル要素 (creator/owner/vendor/instrument)
        -> 同一実体は常に同一UUID。v3(MD5)またはv5(SHA-1)の名前ベース生成を推奨。
           本実装ではv5を採用する。
"""
from __future__ import annotations

import uuid

# MaiMLファイル群で名前ベースUUIDの起点として使う固定namespace UUID。
# (このスクリプト専用の名前空間。値自体に意味はないが、一度決めたら変更しないこと。
#  変更すると同一実体のUUIDが変わってしまい、MaiMLのUUID一貫性ルールに違反する)
MAIML_NAME_UUID_NAMESPACE = uuid.UUID("6f1b1e2a-6e21-4e2a-9b8e-3a5c7d9e1f00")


def new_uuid() -> str:
    """通常のグローバル要素用: RFC4122 v4 (乱数)"""
    return str(uuid.uuid4())


def named_uuid(*parts: str) -> str:
    """
    特定グローバル要素 (creator/owner/vendor/instrument) 用:
    同じpartsを渡せば常に同じUUIDが得られる (v5, SHA-1名前ベース)。

    例:
        named_uuid("elabftw-user", "42", "https://elab.example.org")
        named_uuid("elabftw-vendor", "deltablot")
    """
    name = "|".join(parts)
    return str(uuid.uuid5(MAIML_NAME_UUID_NAMESPACE, name))
