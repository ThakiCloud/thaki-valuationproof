"""valuationproof CLI — offline by default. Network only with an explicit `--fetch` (OpenDART; your own key in env API_K_DART).

  valuationproof demo --out vp_demo                      # fully synthetic chain + 4 synthetic workbooks -> reports (no key, no network)
  valuationproof resolve --synthetic --asof 2025-10-01   # as-of terms (JSON) for the synthetic chain
  valuationproof audit --synthetic --asof 2025-10-01 --excel my.xlsx --out out/
  valuationproof audit --data ./dart_data --fetch --corp <corp_code> --cb <rcept_no> --asof <date> --excel my.xlsx
  valuationproof rcps-check --terms rcps_terms.json      # schema/checklist only — never a verdict
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def _src_args(s, need_cb: bool = False) -> None:
    g = s.add_argument_group("disclosure source (pick one)")
    g.add_argument("--synthetic", action="store_true", help="bundled FULLY SYNTHETIC chain (fictional issuer) — default for demo")
    g.add_argument("--chain-file", help="chain fixture JSON (values + locators + sha256 only, no disclosure text)")
    g.add_argument("--data", default=os.environ.get("VALUATIONPROOF_DART_DATA"),
                   help="local dataset dir (filings_all.json + doc/<rcept_no>.txt). Default $VALUATIONPROOF_DART_DATA")
    g.add_argument("--fetch", action="store_true", help="fill --data with one issuer's CB filings from OpenDART (needs env API_K_DART)")
    s.add_argument("--corp", help="DART corp_code (8 digits). Must match the chain if given")
    s.add_argument("--cb", required=need_cb, help="receipt number (rcept_no) of the original CB filing or of any correction in its chain")
    s.add_argument("--series", help="target series (default: the chain's series)")


def _load_chain(args):
    from . import asof_terms, dart_source, synthetic
    if args.synthetic or not (args.chain_file or args.data):
        if not args.synthetic: raise SystemExit("--synthetic, --chain-file or --data is required")
        ch, texts = synthetic.load()
        if args.cb and args.cb != ch["origin"] and all(v["rcept_no"] != args.cb for v in ch["versions"]): raise SystemExit("CB_NOT_IN_SYNTHETIC_CHAIN")
        return ch, texts
    if args.chain_file:
        ch, texts = dart_source.load_fixture(args.chain_file)
        fx = json.loads(Path(args.chain_file).read_text(encoding="utf-8"))
        if fx.get("synthetic"):
            from . import _synthetic
            _synthetic.register(v["rcept_no"] for v in ch["versions"])
        if args.cb and args.cb != ch["origin"] and all(v["rcept_no"] != args.cb for v in ch["versions"]): raise SystemExit("CB_NOT_IN_FIXTURE")
        if args.corp and ch.get("corp_code") != args.corp: raise SystemExit("CORP_MISMATCH")
        return ch, texts
    if args.fetch:
        if not args.corp: raise SystemExit("--fetch needs --corp")
        print(json.dumps({"fetch": dart_source.fetch_corp(args.corp, args.data)}, ensure_ascii=False))
    if not args.cb: raise SystemExit("--cb is required with --data")
    meta, texts = dart_source.load_dataset(args.data)
    corp = args.corp or next((m["corp_code"] for m in meta if m["rcept_no"] == args.cb), None)
    chains = dart_source.chains_from(meta, texts, corp)
    ch = asof_terms.find_chain(chains, args.corp, args.cb)
    if not ch.get("series"):
        from . import dart_change
        ch["series"] = dart_change.series(texts.get(ch["origin"], "")) if texts.get(ch["origin"]) else None
    return ch, texts


def _summary(r: dict, paths: dict | None = None) -> dict:
    out = {"as_of": r["as_of"], "selected_version": (r.get("selected_version") or {}).get("rcept_no"),
           "excluded_after_asof": [x["rcept_no"] for x in r.get("excluded_after_asof", [])], "counts": r.get("counts"),
           "errors_found": r.get("errors_found"), "needs_human_review": r.get("needs_human_review"), "approval_status": r.get("approval_status")}
    if paths: out["reports"] = paths
    return out


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="valuationproof",
                                description="As-of CB terms from a disclosure/correction chain vs. a valuation workbook -> evidence report "
                                            "(candidates for human review; no auto-fix, no fair value).")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("demo", help="offline demo: synthetic chain + 4 synthetic workbooks -> JSON/MD/HTML reports")
    s.add_argument("--out", default="vp_demo"); s.add_argument("--asof", help="valuation date (default 2025-10-01)")
    s = sub.add_parser("resolve", help="as-of terms with evidence (JSON)")
    _src_args(s); s.add_argument("--asof", required=True); s.add_argument("--out", help="write JSON here (default stdout)")
    s = sub.add_parser("audit", help="as-of terms vs. Excel workbook (read-only) -> evidence report")
    _src_args(s); s.add_argument("--asof", required=True); s.add_argument("--excel", required=True); s.add_argument("--out", default="vp_audit_out")
    s = sub.add_parser("synthetic-dataset", help="write the synthetic chain as a dataset dir (same layout as --data)")
    s.add_argument("--out", required=True)
    s = sub.add_parser("rcps-check", help="RCPS input schema + human checklist (status always CUSTOMER_EVIDENCE_REQUIRED)")
    s.add_argument("--terms", required=True)
    args = p.parse_args(argv)

    if args.cmd == "demo":
        from . import _synthetic, dart_source, demo_v4, synthetic
        ch, texts = synthetic.load(); out = Path(args.out)
        synthetic.write_dataset(out / "synthetic_disclosures")
        dart_source.export_fixture(ch, texts, out / "synthetic_chain_fixture.json", note="SYNTHETIC — fictional issuer, made-up filings")
        fx = json.loads((out / "synthetic_chain_fixture.json").read_text(encoding="utf-8")); fx["synthetic"] = True
        (out / "synthetic_chain_fixture.json").write_text(json.dumps(fx, ensure_ascii=False, indent=1), encoding="utf-8")
        s = demo_v4.run(ch, texts, out, args.asof or synthetic.DEFAULT_ASOF,
                        data_kind="합성 공시 체인(가상 발행사, SYNTHETIC) + 합성 Excel(SYNTH_*) — 실제 공시·실제 평가모형 아님")
        _ = _synthetic
        print(json.dumps({"as_of": s["as_of"], "all_expectations_met": s["all_expectations_met"],
                          "cases": [{k: c[k] for k in ("case", "workbook", "counts", "errors_found", "expectation_met")} for c in s["cases"]]},
                         ensure_ascii=False, indent=1))
    elif args.cmd == "resolve":
        from . import asof_terms
        ch, texts = _load_chain(args)
        r = asof_terms.resolve(ch, texts, args.asof)
        js = json.dumps(r, ensure_ascii=False, indent=1, default=str)
        if args.out: Path(args.out).parent.mkdir(parents=True, exist_ok=True); Path(args.out).write_text(js, encoding="utf-8")
        else: print(js)
    elif args.cmd == "audit":
        from . import audit_v4
        ch, texts = _load_chain(args)
        r = audit_v4.audit(args.excel, ch, texts, args.asof, target_series=args.series)
        if ch.get("synthetic") or str(ch.get("corp_code", "")).startswith("SYNTH"):
            r["data_kind"] = "합성 공시 체인(가상 발행사, SYNTHETIC)"
            r["claim_boundary"] = "[합성 데이터 — 가상 발행사·가상 공시] " + str(r.get("claim_boundary", "")).replace("공개 공시(OpenDART) 기준", "합성 공시 체인 기준")
        paths = audit_v4.write_reports(r, Path(args.out), Path(args.excel).stem + "_report")
        print(json.dumps(_summary(r, paths), ensure_ascii=False, indent=1))
    elif args.cmd == "synthetic-dataset":
        from . import synthetic
        print(synthetic.write_dataset(args.out))
    elif args.cmd == "rcps-check":
        from . import rcps
        print(json.dumps(rcps.check(json.loads(Path(args.terms).read_text(encoding="utf-8"))), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
