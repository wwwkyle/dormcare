"""Load local, single-line environment settings without extra dependencies."""
import os
import re
from pathlib import Path


def load_env(path):
    try:
        content = Path(path).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return
    for line in content.splitlines():
        entry = line.strip()
        if entry.startswith("export "):
            entry = entry[7:].lstrip()
        name, separator, value = entry.partition("=")
        name = name.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            continue
        value = value.strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            closing = value.find(quote, 1)
            if closing < 0 or (value[closing + 1:].strip()
                               and not value[closing + 1:].lstrip().startswith("#")):
                continue
            value = value[1:closing]
        else:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(name, value)
