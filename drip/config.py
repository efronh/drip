import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_policy(overrides: dict | None = None, path: Path | None = None) -> dict:
    policy = json.loads((path or CONFIG_DIR / "policy.json").read_text(encoding="utf-8"))
    return deep_merge(policy, overrides or {})


def load_json(name: str) -> dict:
    return json.loads((CONFIG_DIR / name).read_text(encoding="utf-8"))
