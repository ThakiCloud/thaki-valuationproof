"""Registry of SYNTHETIC receipt numbers.

Synthetic disclosures shipped with this package use made-up receipt numbers. To make sure a report never
renders a link that looks like (or accidentally resolves to) a real DART filing, receipt numbers registered
here are rendered as ``synthetic://<rcept_no>`` instead of a dart.fss.or.kr URL.
"""
from __future__ import annotations

SYNTHETIC_RCEPT: set[str] = set()


def register(rcept_nos) -> None:
    SYNTHETIC_RCEPT.update(str(r) for r in rcept_nos)


class DartView(str):
    def format(self, *args, **kw):  # noqa: A003
        if args and str(args[0]) in SYNTHETIC_RCEPT: return f"synthetic://{args[0]}"
        return str.format(self, *args, **kw)
