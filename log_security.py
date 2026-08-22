from __future__ import annotations
import logging,re

BEARER_PATTERN=re.compile(r"(?i)\bBearer\s+[^\s,;]+")
LABELED_PATTERN=re.compile(r"(?i)(api[_-]?key|authorization|token|secret|password)(\s*[:=]\s*)([^\s,;]+)")
OPENAI_KEY_PATTERN=re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
def redact_sensitive(value:object)->str:
    text=str(value)
    text=BEARER_PATTERN.sub("Bearer [REDACTED]",text)
    text=LABELED_PATTERN.sub(lambda m:m.group(1)+m.group(2)+"[REDACTED]",text)
    return OPENAI_KEY_PATTERN.sub("[REDACTED]",text)
class SensitiveDataFilter(logging.Filter):
    def filter(self,record:logging.LogRecord)->bool:
        record.msg=redact_sensitive(record.getMessage());record.args=()
        return True
