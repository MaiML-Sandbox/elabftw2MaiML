"""
`ExperimentData` (およびそのネストしたdataclass群) を、モジュール/クラスの実体に
依存しないプレーンなJSON互換構造へ変換するヘルパー。

`dataclasses.fields()` によって構造的に (ダックタイピングで) 変換するため、
リファクタリング前後で `ExperimentData` の実クラスが異なるモジュールから
来ていても (例: 回帰テストのbaseline版 vs 現行版)、同じ形をしていれば
同じプレーン表現に変換できる。これにより、golden (期待値) snapshotとの
比較にクラス同一性を要求しない。
"""
from __future__ import annotations

import dataclasses
from datetime import datetime


def to_plain(obj):
    if obj is None:
        return None
    if isinstance(obj, datetime):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_plain(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [to_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_plain(v) for k, v in obj.items()}
    return obj
