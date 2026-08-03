import re
_NAME_TOKEN = r"[a-z][a-z0-9'.-]*"
_NAME_PATTERN = re.compile(r"my name is\s+(" + _NAME_TOKEN + r"(?:\s+" + _NAME_TOKEN + r"){0,3})", re.IGNORECASE)
m = _NAME_PATTERN.search("my name is John")
print("match:", bool(m))
if m:
    print("group(1):", m.group(1))
