"""配置加载：Step 0 五要素全部外置到 config/*.json，禁止在代码里硬编码参数。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "default.json"


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    """字典式配置，支持点路径读取，例如 cfg.get('cost.slippage_bps')。"""

    def __init__(self, data: dict):
        self._d = data

    @classmethod
    def load(cls, path: str | Path | None = None, overrides: dict | None = None) -> "Config":
        p = Path(path) if path else DEFAULT_PATH
        if not p.is_absolute():
            # 相对路径按工程根目录解析，而非进程 cwd
            p = Path(__file__).resolve().parent.parent / p
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if overrides:
            data = _deep_merge(data, overrides)
        return cls(data)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._d
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def __getitem__(self, key: str) -> Any:
        return self._d[key]

    def section(self, key: str) -> dict:
        return copy.deepcopy(self._d.get(key, {}))

    def with_overrides(self, **flat: Any) -> "Config":
        """接受 a.b.c=value 形式的扁平覆盖。"""
        patch: dict = {}
        for key, value in flat.items():
            node = patch
            parts = key.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        return Config(_deep_merge(self._d, patch))

    def to_dict(self) -> dict:
        return copy.deepcopy(self._d)

    def dump(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._d, f, ensure_ascii=False, indent=2)
