"""valuationproof CLI (V4.2). The synthetic modes never touch the network.

  valuationproof demo --out vp_demo                      # fully synthetic chain + 4 synthetic workbooks -> reports (no key, no network)
  valuationproof terms --synthetic --asof 2025-11-20     # V4.2 as-of terms in both modes (restated / as-known) + event coverage
  valuationproof resolve --synthetic --asof 2025-10-01   # as-of terms (JSON) for the synthetic chain
  valuationproof audit --synthetic --asof 2025-10-01 --excel my.xlsx --out out/
  valuationproof audit --data ./dart_data --fetch --corp <corp_code> --cb <rcept_no> --asof <date> --excel my.xlsx
  valuationproof rcps-check --terms rcps_terms.json      # schema/checklist only — never a verdict

Network (real data only, your own OpenDART key in env API_K_DART; the key is never printed or stored):
  --fetch                 one issuer's CB issuance filings into --data
  --adjustments live      (V4.2 default for --data / non-synthetic --chain-file) collect that issuer's conversion-price adjustment
                          filings (전환가액조정) into --event-cache. Without a key, or if any list page fails, the conversion price
                          comes out as EVENT_COVERAGE_UNVERIFIED (needs review) instead of a silent value.
  --adjustments cache|off cache only / no collection (-> conversion price EVENT_COVERAGE_UNVERIFIED). --engine 4.1 = V4.1 behaviour.
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
    v = s.add_argument_group("V4.2 options")
    v.add_argument("--engine", choices=("4.2", "4.1"), default="4.2", help="4.1 = reproduce V4.1 (no adjustment collection / coverage checks)")
    v.add_argument("--adjustments", choices=("live", "cache", "off"), default="live",
                   help="conversion-price adjustment filings: live (default; synthetic modes use the bundled in-memory source) · cache · off")
    v.add_argument("--event-cache", default=os.environ.get("VALUATIONPROOF_EVENT_CACHE"),
                   help="adjustment filing cache dir (default $VALUATIONPROOF_EVENT_CACHE, <data>/adjust_cache, or a temp dir)")
    v.add_argument("--collect-end", help="collection end date YYYY-MM-DD (default today; synthetic modes: fixed 2026-01-31) — knowledge point of 'restated'")
    v.add_argument("--knowledge-cutoff", help="knowledge cutoff for --mode as-known, YYYY-MM-DD (default = --asof)")


def _synthetic_collect(args, ch):
    """Synthetic chains: V4.2 collection runs the real collector against the bundled in-memory source (never the network)."""
    from . import adjust_events, synthetic
    if args.engine == "4.1": return ch
    if args.adjustments == "off": return adjust_events.not_collected(ch, "ADJUSTMENTS_OFF(--adjustments off)")
    return synthetic.collect_v42(ch, cache_dir=args.event_cache) if ch.get("corp_code") == synthetic.CORP else \
        adjust_events.not_collected(ch, "SYNTHETIC_CHAIN_UNKNOWN")


def _collect(args, ch, corp_chains):
    """V4.2 default path (real data): collect the issuer's conversion-price adjustment filings and attach them (adjust_events).
    If that is not possible the conversion price is EVENT_COVERAGE_UNVERIFIED — never a silent value."""
    import sys, tempfile
    from datetime import date
    from . import adjust_events, asof_terms
    if args.engine == "4.1": return ch
    if ch.get("adjustment_coverage") and args.adjustments != "live": return ch          # collection record inside the fixture (offline)
    if args.adjustments == "off": return adjust_events.not_collected(ch, "ADJUSTMENTS_OFF(--adjustments off)")
    if not ch.get("corp_code"): return adjust_events.not_collected(ch, "NO_CORP_CODE")
    cache = args.event_cache or (str(Path(args.data) / "adjust_cache") if getattr(args, "data", None) else tempfile.mkdtemp(prefix="vp_adj_"))
    end = asof_terms.ymd(args.collect_end) if args.collect_end else date.today().strftime("%Y%m%d")
    rec = adjust_events.collect_corp(ch["corp_code"], adjust_events.chain_start(ch), end, cache, offline=args.adjustments == "cache")
    print(json.dumps({"adjustment_collection": {"corp_code": rec["corp_code"], "reports": len(rec["reports"]), "api_calls": rec["api_calls"],
                      "windows_ok": all(w["ok"] for w in rec["list_windows"])}}, ensure_ascii=False), file=sys.stderr)
    return adjust_events.attach(ch, rec, adjust_events.series_counts(corp_chains))


def _load_chain(args):
    from . import asof_terms, dart_source, synthetic
    if args.synthetic or not (args.chain_file or args.data):
        if not args.synthetic: raise SystemExit("--synthetic, --chain-file or --data is required")
        ch, texts = synthetic.load()
        if args.cb and args.cb != ch["origin"] and all(v["rcept_no"] != args.cb for v in ch["versions"]): raise SystemExit("CB_NOT_IN_SYNTHETIC_CHAIN")
        return _synthetic_collect(args, ch), texts
    if args.chain_file:
        ch, texts = dart_source.load_fixture(args.chain_file)
        fx = json.loads(Path(args.chain_file).read_text(encoding="utf-8"))
        if args.cb and args.cb != ch["origin"] and all(v["rcept_no"] != args.cb for v in ch["versions"]): raise SystemExit("CB_NOT_IN_FIXTURE")
        if args.corp and ch.get("corp_code") != args.corp: raise SystemExit("CORP_MISMATCH")
        if fx.get("synthetic"):
            from . import _synthetic
            _synthetic.register(v["rcept_no"] for v in ch["versions"])
            _synthetic.register(a["rcept_no"] for a in ch.get("adjustments") or [])
            ch["synthetic"] = True
            return _synthetic_collect(args, ch), texts
        return _collect(args, ch, [ch]), texts
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
    return _collect(args, ch, [c for c in chains.values() if c.get("corp_code") == ch.get("corp_code")]), texts


def _resolve_kw(args, mode: str | None) -> dict:
    if args.engine == "4.1": return {"engine": "4.1"}
    m = (mode or "restated").replace("-", "_")
    return {"engine": "4.2", "mode": m, "knowledge_cutoff": args.knowledge_cutoff if m == "as_known" else None}


def _terms_view(r: dict) -> dict:
    return {"mode": r.get("mode", "v41"), "knowledge_cutoff": r.get("knowledge_cutoff"), "status": r["status"],
            "selected_version": (r.get("selected_version") or {}).get("rcept_no"), "event_coverage": r.get("event_coverage"),
            "values_are": r.get("values_are"),
            "terms": {f: {k: t.get(k) for k in ("value", "mode", "knowledge_cutoff", "effective_status", "coverage_status", "status_ko",
                                                "alternative_values", "value_basis", "contract_effective_confirmed", "review_required")}
                      | {"source": t["source"]["rcept_no"], "adjusted_by": (t.get("adjusted_by") or {}).get("rcept_no"),
                         "bitemporal": (t.get("adjusted_by") or {}).get("bitemporal")}
                      for f, t in (r.get("terms") or {}).items()}}


def v42_demo() -> dict:
    """V4.2 synthetic scenarios: the same adjustment event under restated / as-known, and two broken collections."""
    from . import asof_terms, synthetic
    ch, tx = synthetic.load_v42(); T = synthetic.V42_ASOF
    def cp(c, **kw):
        r = asof_terms.resolve(c, tx, T, **kw); t = r["terms"]["conversion_price_krw"]
        return {"value": t["value"], "coverage_status": t.get("coverage_status"), "status_ko": t.get("status_ko"),
                "mode": t.get("mode"), "valuation_asof": t.get("valuation_asof"), "knowledge_cutoff": t.get("knowledge_cutoff"),
                "adjusted_by": (t.get("adjusted_by") or {}).get("rcept_no"), "revised_by": (t.get("adjusted_by") or {}).get("revised_by"),
                "bitemporal": (t.get("adjusted_by") or {}).get("bitemporal"), "coverage_reasons": t["event_coverage"]["reasons"],
                "value_basis": t.get("value_basis"), "auto_verdict_allowed": t.get("auto_verdict_allowed", True)}
    rows = [("restated (all events published by %s)" % synthetic.COLLECT_END, ch, {}),
            ("as-known, knowledge cutoff = as-of", ch, {"mode": "as_known"}),
            ("as-known, knowledge cutoff 2025-11-30", ch, {"mode": "as_known", "knowledge_cutoff": "2025-11-30"}),
            ("collection broken: list request failed (C1)", synthetic.load_v42(fail="list_page")[0], {}),
            ("collection off: --adjustments off (C0)", synthetic.load_v42(fail="off")[0], {})]
    return {"kind": "valuationproof.v42_synthetic_demo", "synthetic": True, "valuation_asof": T, "collected_through": synthetic.COLLECT_END,
            "events": [{"rcept_no": a["rcept_no"], "known_at": a["known_at"], "effective_at": a["effective_at"], "before_krw": a["before_krw"],
                        "after_krw": a["conversion_price_krw"], "revisions": [{"rcept_no": x["rcept_no"], "known_at": x["known_at"],
                                                                                "after_krw": x["conversion_price_krw"]} for x in a["revisions"]]}
                       for a in ch["adjustments"]],
            "scenarios": [{"scenario": n} | cp(c, **kw) for n, c, kw in rows],
            "note": "SYNTHETIC — fictional issuer and filings. Values are disclosure-based estimates (contract effective date not confirmed)."}


def _summary(r: dict, paths: dict | None = None) -> dict:
    out = {"as_of": r["as_of"], "selected_version": (r.get("selected_version") or {}).get("rcept_no"),
           "excluded_after_asof": [x["rcept_no"] for x in r.get("excluded_after_asof", [])], "counts": r.get("counts"),
           "errors_found": r.get("errors_found"), "needs_human_review": r.get("needs_human_review"), "approval_status": r.get("approval_status"),
           "engine": r.get("engine"), "mode": r.get("mode"), "knowledge_cutoff": r.get("knowledge_cutoff"),
           "event_coverage": (r.get("event_coverage") or {}).get("status"), "values_are": r.get("values_are")}
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
    s.add_argument("--mode", choices=("restated", "as-known"), default="restated")
    s = sub.add_parser("terms", help="V4.2 as-of terms only, per mode (restated / as-known / both) with event coverage")
    _src_args(s); s.add_argument("--asof", required=True); s.add_argument("--out", help="also write JSON here")
    s.add_argument("--mode", choices=("restated", "as-known", "both"), default="both")
    s = sub.add_parser("audit", help="as-of terms vs. Excel workbook (read-only) -> evidence report")
    _src_args(s); s.add_argument("--asof", required=True); s.add_argument("--excel", required=True); s.add_argument("--out", default="vp_audit_out")
    s.add_argument("--mode", choices=("restated", "as-known"), default="restated")
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
        v42 = v42_demo(); (out / "v42_synthetic_demo.json").write_text(json.dumps(v42, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"as_of": s["as_of"], "all_expectations_met": s["all_expectations_met"],
                          "cases": [{k: c[k] for k in ("case", "workbook", "counts", "errors_found", "expectation_met")} for c in s["cases"]],
                          "v42_synthetic_demo": [{k: x[k] for k in ("scenario", "value", "coverage_status")} for x in v42["scenarios"]]},
                         ensure_ascii=False, indent=1))
    elif args.cmd == "resolve":
        from . import asof_terms
        ch, texts = _load_chain(args)
        r = asof_terms.resolve(ch, texts, args.asof, **_resolve_kw(args, args.mode))
        js = json.dumps(r, ensure_ascii=False, indent=1, default=str)
        if args.out: Path(args.out).parent.mkdir(parents=True, exist_ok=True); Path(args.out).write_text(js, encoding="utf-8")
        else: print(js)
    elif args.cmd == "terms":
        from . import asof_terms
        ch, texts = _load_chain(args)
        modes = ["v41"] if args.engine == "4.1" else (["restated", "as_known"] if args.mode == "both" else [args.mode])
        out = {"as_of": asof_terms.iso(args.asof), "engine": args.engine,
               "results": [_terms_view(asof_terms.resolve(ch, texts, args.asof, **_resolve_kw(args, None if m == "v41" else m))) for m in modes]}
        js = json.dumps(out, ensure_ascii=False, indent=1, default=str)
        if args.out: Path(args.out).parent.mkdir(parents=True, exist_ok=True); Path(args.out).write_text(js, encoding="utf-8")
        print(js)
    elif args.cmd == "audit":
        from . import audit_v4
        ch, texts = _load_chain(args)
        r = audit_v4.audit(args.excel, ch, texts, args.asof, target_series=args.series, **_resolve_kw(args, args.mode))
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
