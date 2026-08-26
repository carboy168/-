from __future__ import annotations

import hashlib
import json
from pathlib import Path

from engineering_knowledge.json_schema import validate_instance


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "data" / "engineering_contracts.json"
REGISTRY_SCHEMA = ROOT / "data" / "engineering_contracts.schema.json"


class ContractCompatibilityError(ValueError):
    pass


def load_contract_registry(path: Path | None = None) -> dict:
    registry_path = (path or DEFAULT_REGISTRY).resolve()
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    schema = json.loads(REGISTRY_SCHEMA.read_text(encoding="utf-8"))
    validate_instance(registry, schema)
    current = registry["current_write_version"]
    readable = registry["readable_versions"]
    schemas = registry["schemas"]
    if current not in readable or set(readable) != set(schemas):
        raise ContractCompatibilityError("契约注册表的可读版本、写入版本和 Schema 清单不一致。")
    for version, item in schemas.items():
        schema_path = (ROOT / item["path"]).resolve()
        try:
            schema_path.relative_to(ROOT)
        except ValueError as exc:
            raise ContractCompatibilityError(f"Schema {version} 路径越出源码根目录。") from exc
        if not schema_path.is_file():
            raise ContractCompatibilityError(f"Schema {version} 文件不存在。")
        schema_document = json.loads(schema_path.read_text(encoding="utf-8"))
        canonical = json.dumps(schema_document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        if digest != item["sha256"]:
            raise ContractCompatibilityError(f"冻结 Schema {version} 内容发生变化，必须发布新契约版本。")
        if schema_document.get("properties", {}).get("schema_version", {}).get("const") != version:
            raise ContractCompatibilityError(f"Schema {version} 的版本常量不一致。")
    return registry


def schema_for_version(version: str, registry: dict | None = None) -> dict:
    registry = registry or load_contract_registry()
    if version not in registry["readable_versions"]:
        raise ContractCompatibilityError(f"不支持读取 Engineering Knowledge 契约版本：{version}")
    path = ROOT / registry["schemas"][version]["path"]
    return json.loads(path.read_text(encoding="utf-8"))


def assert_readable_version(version: str, registry: dict | None = None) -> None:
    registry = registry or load_contract_registry()
    if version not in registry["readable_versions"]:
        raise ContractCompatibilityError(f"当前不能读取 Engineering Knowledge 契约版本：{version}")


def assert_writable_version(version: str, registry: dict | None = None) -> None:
    registry = registry or load_contract_registry()
    if version != registry["current_write_version"]:
        raise ContractCompatibilityError(
            f"当前只能写入 {registry['current_write_version']}，不能静默写入 {version}。"
        )
