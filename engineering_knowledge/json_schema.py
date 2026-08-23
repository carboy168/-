from __future__ import annotations

import json,re
from pathlib import Path
from typing import Any


class JsonSchemaValidationError(ValueError):pass


def _resolve(root:dict,reference:str)->dict:
    if not reference.startswith("#/"):raise JsonSchemaValidationError(f"仅支持本地 $ref：{reference}")
    value:Any=root
    for token in reference[2:].split("/"):
        value=value[token.replace("~1","/").replace("~0","~")]
    return value


def _type_ok(value:Any,expected:str)->bool:
    checks={
        "object":lambda x:isinstance(x,dict),"array":lambda x:isinstance(x,list),
        "string":lambda x:isinstance(x,str),"integer":lambda x:isinstance(x,int) and not isinstance(x,bool),
        "number":lambda x:isinstance(x,(int,float)) and not isinstance(x,bool),"boolean":lambda x:isinstance(x,bool),
        "null":lambda x:x is None,
    }
    return expected in checks and checks[expected](value)


def validate_instance(instance:Any,schema:dict,root_schema:dict|None=None,path:str="$")->None:
    root=root_schema or schema
    if "$ref" in schema:return validate_instance(instance,_resolve(root,schema["$ref"]),root,path)
    if "anyOf" in schema:
        for candidate in schema["anyOf"]:
            try:validate_instance(instance,candidate,root,path);return
            except JsonSchemaValidationError:pass
        raise JsonSchemaValidationError(f"{path}: 不符合任何 anyOf 分支")
    if "const" in schema and instance!=schema["const"]:raise JsonSchemaValidationError(f"{path}: 必须等于 {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:raise JsonSchemaValidationError(f"{path}: 不在允许枚举中")
    expected=schema.get("type")
    if expected and not _type_ok(instance,expected):raise JsonSchemaValidationError(f"{path}: 类型应为 {expected}")
    if isinstance(instance,dict):
        missing=set(schema.get("required",[]))-set(instance)
        if missing:raise JsonSchemaValidationError(f"{path}: 缺少字段 {sorted(missing)}")
        properties=schema.get("properties",{});additional=schema.get("additionalProperties",True)
        for key,value in instance.items():
            if key in properties:validate_instance(value,properties[key],root,f"{path}.{key}")
            elif additional is False:raise JsonSchemaValidationError(f"{path}: 不允许字段 {key}")
            elif isinstance(additional,dict):validate_instance(value,additional,root,f"{path}.{key}")
    if isinstance(instance,list):
        if len(instance)<schema.get("minItems",0):raise JsonSchemaValidationError(f"{path}: 项目数量不足")
        if "maxItems" in schema and len(instance)>schema["maxItems"]:raise JsonSchemaValidationError(f"{path}: 项目数量过多")
        if schema.get("uniqueItems") and len({json.dumps(x,sort_keys=True,ensure_ascii=False) for x in instance})!=len(instance):raise JsonSchemaValidationError(f"{path}: 项目必须唯一")
        if "items" in schema:
            for index,value in enumerate(instance):validate_instance(value,schema["items"],root,f"{path}[{index}]")
    if isinstance(instance,str):
        if len(instance)<schema.get("minLength",0):raise JsonSchemaValidationError(f"{path}: 字符串过短")
        if "maxLength" in schema and len(instance)>schema["maxLength"]:raise JsonSchemaValidationError(f"{path}: 字符串过长")
        if schema.get("pattern") and not re.search(schema["pattern"],instance):raise JsonSchemaValidationError(f"{path}: 不符合 pattern")
    if isinstance(instance,(int,float)) and not isinstance(instance,bool):
        if "minimum" in schema and instance<schema["minimum"]:raise JsonSchemaValidationError(f"{path}: 小于最小值")
        if "maximum" in schema and instance>schema["maximum"]:raise JsonSchemaValidationError(f"{path}: 大于最大值")


def validate_json_file(data_path:Path,schema_path:Path)->dict:
    instance=json.loads(data_path.read_text(encoding="utf-8"));schema=json.loads(schema_path.read_text(encoding="utf-8"))
    validate_instance(instance,schema)
    return instance
