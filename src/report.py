"""Console summary table + markdown report."""
import datetime
import json

from common import C, CONVERSE_MODEL, MANTLE_BASE, OPENAI_MODEL_MANTLE, REGION, RESULTS_DIR


COLS = [("phase", 42), ("case", 16), ("expect", 26), ("actual", 30), ("ok", 6)]


def _status(r):
    if r["ok"]:
        return "PASS"
    return "DIFF" if r.get("advisory") else "FAIL"


def console(results):
    print(f"\n{C.BOLD}SUMMARY{C.OFF}")
    header = "  " + "".join(name.upper().ljust(w) for name, w in COLS)
    print(C.BOLD + header + C.OFF)
    print("  " + "-" * (sum(w for _, w in COLS)))
    for r in results:
        status = _status(r)
        color = C.GREEN if status == "PASS" else (C.YELLOW if status == "DIFF" else C.RED)
        row = "  "
        for name, w in COLS:
            val = status if name == "ok" else str(r.get(name, ""))
            row += val[: w - 1].ljust(w)
        print(color + row + C.OFF)

    hard = [r for r in results if not r["ok"] and not r.get("advisory")]
    soft = [r for r in results if not r["ok"] and r.get("advisory")]
    passed = [r for r in results if r["ok"]]
    print(f"\n  total={len(results)}  {C.GREEN}pass={len(passed)}{C.OFF}  "
          f"{C.YELLOW}advisory-diff={len(soft)}{C.OFF}  {C.RED}fail={len(hard)}{C.OFF}")
    return len(hard)


def markdown(results, meta):
    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    lines = [
        "# Bedrock Guardrails 验证报告",
        "",
        f"- 生成时间：{ts}",
        f"- Region：`{REGION}`",
        f"- Converse 模型：`{CONVERSE_MODEL}`",
        f"- mantle 端点：`{MANTLE_BASE}`，模型 `{OPENAI_MODEL_MANTLE}`",
        f"- Guardrail：Standard=`{meta['standard']}` 版本 `{meta.get('standard_version', 'DRAFT')}`，"
        f"Classic=`{meta['classic']}` 版本 `{meta.get('classic_version', 'DRAFT')}`",
        "",
        "## 结果汇总",
        "",
        "| 阶段 | 用例 | 语言 | 方向 | 期望 | 实际 | 命中策略 | 延迟ms | 判定 | 备注 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            r["phase"], r["case"], r.get("lang", "-"), r.get("source", "-"),
            r["expect"], str(r["actual"]).replace("|", "/"),
            str(r["hits"]).replace("|", "/"), r.get("latency") or "-",
            _status(r), str(r.get("note", "-")).replace("|", "/"),
        ))

    hard = sum(1 for r in results if not r["ok"] and not r.get("advisory"))
    soft = sum(1 for r in results if not r["ok"] and r.get("advisory"))
    lines += [
        "",
        f"合计 {len(results)} 项：PASS {sum(1 for r in results if r['ok'])}，"
        f"DIFF(仅提示) {soft}，FAIL {hard}",
        "",
        "## 计费单元样本（ApplyGuardrail usage）",
        "",
        "```json",
        json.dumps([{"case": r["case"], "units": r["units"]}
                    for r in results if r.get("units")][:6], ensure_ascii=False, indent=2),
        "```",
    ]
    path = RESULTS_DIR / "report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (RESULTS_DIR / "raw_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"  report  -> {path}")
    print(f"  raw     -> {RESULTS_DIR / 'raw_results.json'}")
