"""Value normalisation for DART form cells (date -> ISO, KRW -> integer string, % -> float string)."""
from __future__ import annotations

import re

_EMPTY = ("", "-", "--", "해당사항없음", "해당사항 없음", "N/A", "없음")


def default_norm(field, v):
    if v is None: return None
    s = str(v).strip()
    if s in _EMPTY: return None
    if field.endswith("_date"):
        n = re.findall(r"\d+", s)
        if len(n) == 1 and len(n[0]) == 8: n = [n[0][:4], n[0][4:6], n[0][6:]]
        return f"{int(n[0]):04d}-{int(n[1]):02d}-{int(n[2]):02d}" if len(n) >= 3 else None
    if field.endswith("_krw"):
        m = re.search(r"\d[\d,]*", s); return str(int(m.group().replace(",", ""))) if m else None
    if field.endswith("_pct"):
        m = re.search(r"-?\d+(?:\.\d+)?", s); return str(float(m.group())) if m else None
    return s
