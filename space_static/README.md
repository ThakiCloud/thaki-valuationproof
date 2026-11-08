---
title: ValuationProof Demo
emoji: 📑
colorFrom: indigo
colorTo: gray
sdk: static
app_file: index.html
pinned: false
license: apache-2.0
short_description: As-of CB terms vs valuation Excel — synthetic demo
---

# ValuationProof Demo (synthetic, runs in your browser)

Static demo of [ThakiCloud/thaki-valuationproof](https://github.com/ThakiCloud/thaki-valuationproof) (V4.1).
The package code in `valuationproof/` runs **in your browser** via Pyodide; nothing is sent to a server. Pre-generated reports are in `reports/`.

- **All data is synthetic**: a fictional issuer (`SYNTH001`) with a made-up disclosure chain (1 original + 3 corrections) and 4 synthetic workbooks.
  No real company, filing or accountant workbook. No DART key, no LLM.
- A field whose value depends on an effective date the disclosures cannot confirm is reported as **`확인 필요(효력일 미확정)` / needs review** with candidate values, and gets no automatic verdict.
- Findings are **candidates for human review** (`approval_status = PENDING`). No auto-fix, no fair-value calculation.

**Limits (this is the fixed V4.1 release):**
- Conversion-price adjustment (전환가액조정) filings are **not auto-collected**. They are applied only when you supply them (`chain["adjustments"]`).
- It does **not** claim to fully audit every term of real DART filings.
- "0 silent errors" holds **only within the measured sample and the two interpretations (IA/IB)**. It is not 100% accuracy: uncertain terms were routed to `확인 필요` (needs review).
- The cost: **24.3%** of quarter-end cases (138/567) need human review.
- **Not evaluated on real accountant workbooks.**
- V4.2 (adjustment auto-collection, bitemporal history) is **in development (roadmap)** and not part of this release.

Third-party runtime loaded by the page: Pyodide (MPL-2.0) from cdn.jsdelivr.net, and openpyxl (MIT) from PyPI.
See the GitHub README for the "Validated / Not validated" scope.
