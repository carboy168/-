from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
OBJECTS_FILE=ROOT/"data"/"engineering_objects.json"
RELATIONS_FILE=ROOT/"data"/"engineering_relations.json"


def _load(path:Path)->dict[str,Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_object_catalog(path:Path|None=None)->dict[str,Any]:
    pack=_load(path or OBJECTS_FILE)
    if not isinstance(pack.get("schema_version"),int) or not isinstance(pack.get("object_types"),list):raise ValueError("EngineeringObject 配置结构无效。")
    required={"object_type","canonical_name","aliases","profession","system","topic_hints"};ids=set()
    for item in pack["object_types"]:
        missing=required-set(item)
        if missing:raise ValueError(f"EngineeringObject {item.get('object_type','?')} 缺少字段：{sorted(missing)}")
        if item["object_type"] in ids:raise ValueError(f"EngineeringObject ID 重复：{item['object_type']}")
        if not item["aliases"]:raise ValueError(f"EngineeringObject {item['object_type']} 没有 aliases。")
        ids.add(item["object_type"])
    return pack


def load_relation_catalog(path:Path|None=None,object_pack:dict|None=None)->dict[str,Any]:
    pack=_load(path or RELATIONS_FILE);object_pack=object_pack or load_object_catalog()
    if not isinstance(pack.get("relation_types"),list) or not isinstance(pack.get("rules"),list):raise ValueError("EngineeringRelation 配置结构无效。")
    relation_types=set(pack["relation_types"]);object_types={x["object_type"] for x in object_pack["object_types"]};rule_ids=set()
    required={"rule_id","relation_type","expressions","mode","subject_types","object_types","max_distance","score","topic_hints"}
    for rule in pack["rules"]:
        missing=required-set(rule)
        if missing:raise ValueError(f"Relation rule {rule.get('rule_id','?')} 缺少字段：{sorted(missing)}")
        if rule["rule_id"] in rule_ids:raise ValueError(f"Relation rule ID 重复：{rule['rule_id']}")
        if rule["relation_type"] not in relation_types:raise ValueError(f"未知 relation_type：{rule['relation_type']}")
        unknown = (
            set(rule["subject_types"]) | set(rule["object_types"])
        ) - object_types
        if unknown:raise ValueError(f"Relation rule {rule['rule_id']} 引用未知对象：{sorted(unknown)}")
        if rule["mode"] not in {"between","two_before","container_before_all"}:raise ValueError(f"Relation rule mode 无效：{rule['mode']}")
        rule_ids.add(rule["rule_id"])
    return pack
