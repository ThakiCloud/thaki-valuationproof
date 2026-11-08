"""ValuationProof demo (Hugging Face Space) — offline, FULLY SYNTHETIC by default. No DART key, no network, no LLM."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import gradio as gr

from valuationproof import asof_terms, audit_v4, cli, synthetic

WORK = Path(tempfile.mkdtemp(prefix="vp_"))
DEMO = WORK / "demo"
NOTICE = ("**모든 데이터는 합성(SYNTHETIC)입니다.** 가상 발행사 `SYNTH001`의 가상 공시 체인(원공시 1 + 정정 3)과 합성 평가 Excel 4종입니다. "
          "실제 회사·실제 공시·실제 회계사 평가모형이 아닙니다. 결과는 사람 검토용 후보(PENDING)이며 자동 수정·공정가치 계산은 하지 않습니다. "
          "All data is synthetic; findings are review candidates, not verdicts.")
LIMITS = ("\n\n**한계 (V4.1 고정판)** — 전환가액조정 공시는 자동 수집하지 않습니다(`chain[\"adjustments\"]` 로 넣을 때만 반영). "
          "실제 DART 전체 조건을 완전 감사한다고 주장하지 않습니다. 공개 표본에서 '표시 없는 오류 0건'은 그 표본·두 해석(IA/IB) 범위 안의 결과이며 "
          "정확도 100% 가 아닙니다(불확실한 조건을 `확인 필요`로 돌린 결과). 그 대가로 분기말 24.3%(138/567)는 사람 확인이 필요합니다. "
          "실제 회계사 Excel 로는 평가하지 않았습니다. V4.2(조정공시 자동 수집·bitemporal)는 개발 중인 로드맵입니다.")


def _run_demo(asof: str):
    if DEMO.exists(): shutil.rmtree(DEMO)
    import contextlib, io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): cli.main(["demo", "--out", str(DEMO), "--asof", asof or synthetic.DEFAULT_ASOF])
    s = json.loads((DEMO / "demo_summary.json").read_text(encoding="utf-8"))
    rows = [[c["case"], c["workbook"], c["counts"]["일치"], c["counts"]["불일치"], c["counts"]["누락"], c["counts"]["미확인"],
             c["errors_found"], "예" if c["expectation_met"] else "아니오"] for c in s["cases"]]
    return s, rows


def run_demo(asof):
    s, rows = _run_demo(asof)
    first = DEMO / (Path(s["cases"][0]["workbook"]).stem + "_report.html")
    files = sorted(str(p) for p in DEMO.glob("SYNTH_*"))
    return (f"기준일 {s['as_of']} · 적용 공시 {s['cases'][0]['selected_version']} · 기준일 뒤 제외 {', '.join(s['cases'][0]['excluded_after_asof']) or '없음'}"
            f" · 기대 결과 충족: {s['all_expectations_met']}", rows, first.read_text(encoding="utf-8"), files)


def show_case(name):
    p = DEMO / f"{Path(name).stem}_report.html"
    return p.read_text(encoding="utf-8") if p.exists() else "먼저 데모를 실행하세요."


def audit_upload(file, asof):
    if file is None: return "xlsx 파일을 올리세요.", None, []
    ch, tx = synthetic.load()
    out = WORK / "upload"; out.mkdir(exist_ok=True)
    r = audit_v4.audit(file, ch, tx, asof or synthetic.DEFAULT_ASOF)
    r["claim_boundary"] = "[합성 공시 체인 기준] " + str(r.get("claim_boundary", ""))
    paths = audit_v4.write_reports(r, out, Path(file).stem + "_report", "[합성 공시 체인 대비] 업로드 워크북")
    return (json.dumps({k: r.get(k) for k in ("as_of", "counts", "errors_found", "needs_human_review", "approval_status")}, ensure_ascii=False),
            Path(paths["html"]).read_text(encoding="utf-8"), [paths["json"], paths["md"], paths["html"]])


def resolve_terms(asof):
    ch, tx = synthetic.load()
    r = asof_terms.resolve(ch, tx, asof or synthetic.EFFECTIVE_DATE_ASOF)
    if r["status"] != "RESOLVED": return f"상태 {r['status']} — 기준일이 원 결의일보다 앞서 조건을 내지 않습니다.", []
    rows = [[t["label_ko"], json.dumps(t["value"], ensure_ascii=False) if isinstance(t["value"], dict) else t["value"],
             t.get("status_ko") or ("확인 필요(효력일 미확정)" if t.get("effective_status") == "UNCONFIRMED_DEPENDS" else "자동 대조 가능"),
             ", ".join(str(v) for v in (t.get("alternative_values") or [])), (t.get("date_key") or {}).get("basis"),
             (t.get("date_key") or {}).get("key_dt"), "예" if (t.get("date_key") or {}).get("date_key_fallback") else ""] for t in r["terms"].values()]
    sv = r["selected_version"]
    return (f"기준일 {r['as_of']} → 버전 {sv['rcept_no']} · 효력일 미확정 필드 {len(r.get('unconfirmed_fields') or [])}개 · "
            f"제외 {', '.join(x['rcept_no'] + '(' + x['reason'] + ')' for x in r['excluded_after_asof']) or '없음'}"), rows


with gr.Blocks(title="ValuationProof Demo") as demo:
    gr.Markdown("# ValuationProof — 기준일 CB 조건 × 평가 Excel 대조 (합성 데모)\n" + NOTICE + LIMITS +
                "\n\n코드: [github.com/ThakiCloud/thaki-valuationproof](https://github.com/ThakiCloud/thaki-valuationproof) · Apache-2.0")
    with gr.Tab("합성 데모 4종"):
        asof = gr.Textbox(value=synthetic.DEFAULT_ASOF, label="평가 기준일 (YYYY-MM-DD)")
        btn = gr.Button("데모 실행", variant="primary")
        status = gr.Markdown()
        table = gr.Dataframe(headers=["케이스", "워크북", "일치", "불일치", "누락", "미확인", "오류 후보", "기대 충족"], interactive=False)
        case = gr.Dropdown(["SYNTH_normal.xlsx", "SYNTH_stale_correction.xlsx", "SYNTH_refix_maturity_typo.xlsx", "SYNTH_transposed_synonym.xlsx"],
                           value="SYNTH_normal.xlsx", label="보고서 보기")
        report = gr.HTML()
        files = gr.File(label="합성 워크북·보고서 다운로드", file_count="multiple")
        btn.click(run_demo, asof, [status, table, report, files])
        case.change(show_case, case, report)
    with gr.Tab("내 워크북 올려 보기 (합성 체인 대비)"):
        gr.Markdown("올린 .xlsx 는 읽기 전용으로 열고 이 Space 의 임시 폴더에만 둡니다. **합성 공시 체인과 대조**하므로 실제 발행사 검증이 아닙니다. "
                    "실제 공시 대조는 로컬에서 본인 DART API 키로 CLI 를 쓰세요(README).")
        up = gr.File(label=".xlsx", file_types=[".xlsx"], type="filepath")
        asof2 = gr.Textbox(value=synthetic.DEFAULT_ASOF, label="평가 기준일")
        b2 = gr.Button("대조")
        s2 = gr.Code(language="json"); h2 = gr.HTML(); f2 = gr.File(file_count="multiple")
        b2.click(audit_upload, [up, asof2], [s2, h2, f2])
    with gr.Tab("효력일 미확정 예시"):
        gr.Markdown("합성 사슬: C1(전환가액 10,000→8,500)의 이사회결의일은 2025-06-16, 접수일은 2025-06-20 입니다. 기준일이 그 사이(예: 2025-06-18)면 "
                    "공시만으로 효력일을 확정할 수 없어 **'확인 필요(효력일 미확정)'** 로 내고 자동 판정하지 않습니다. 2025-09-29 는 기재 정정(C2) 소급 여부에 값이 달립니다.")
        asof3 = gr.Textbox(value=synthetic.EFFECTIVE_DATE_ASOF, label="평가 기준일")
        b3 = gr.Button("기준일 조건 보기")
        s3 = gr.Markdown()
        t3 = gr.Dataframe(headers=["조건", "값", "상태", "후보 값", "날짜 키 근거", "날짜 키", "접수일 대체"], interactive=False)
        b3.click(resolve_terms, asof3, [s3, t3])
    demo.load(run_demo, asof, [status, table, report, files])

if __name__ == "__main__":
    demo.launch()
