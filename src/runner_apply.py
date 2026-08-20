"""Path A — ApplyGuardrail: model-independent evaluation of INPUT / OUTPUT text."""
import time

import cases as CS
from common import C, hit_policies, runtime, usage_of


def evaluate(gid, case, version="DRAFT"):
    t0 = time.time()
    resp = runtime.apply_guardrail(
        guardrailIdentifier=gid,
        guardrailVersion=version,
        source=case["source"],
        content=CS.content_of(case),
        outputScope="FULL",
    )
    wall_ms = int((time.time() - t0) * 1000)
    assessments = resp.get("assessments", [])
    latency = None
    for a in assessments:
        latency = a.get("invocationMetrics", {}).get("guardrailProcessingLatency") or latency
    return resp, hit_policies(assessments), latency or wall_ms


def check(case, action, hits, out_text):
    problems = []
    if action != case["expect_action"]:
        problems.append(f"action={action} expected={case['expect_action']}")
    for want in case.get("expect_hits", []):
        if not any(want in h for h in hits):
            problems.append(f"missing hit '{want}'")
    if case.get("expect_output") and case["expect_output"] not in out_text:
        problems.append(f"output missing '{case['expect_output']}'")
    return problems


def run(gid, phase, case_ids=None, advisory=False, version="DRAFT"):
    selected = [CS.by_id(i) for i in case_ids] if case_ids else CS.CASES
    results = []
    for case in selected:
        resp, hits, latency = evaluate(gid, case, version)
        action = resp["action"]
        out_text = " | ".join(o.get("text", "") for o in resp.get("outputs", []))
        problems = check(case, action, hits, out_text)
        ok = not problems
        mark = f"{C.GREEN}PASS{C.OFF}" if ok else (
            f"{C.YELLOW}DIFF{C.OFF}" if advisory else f"{C.RED}FAIL{C.OFF}")
        print(f"  [{mark}] {case['id']:<14} {case['desc'][:44]:<46} "
              f"{action:<22} {latency}ms")
        if hits:
            print(f"         {C.DIM}hits: {', '.join(hits)}{C.OFF}")
        if out_text:
            print(f"         {C.DIM}output: {out_text[:110]}{C.OFF}")
        if problems:
            print(f"         {C.YELLOW}note: {'; '.join(problems)}{C.OFF}")
        results.append(dict(
            phase=phase, case=case["id"], desc=case["desc"], lang=case["lang"],
            source=case["source"], expect=case["expect_action"], actual=action,
            hits=", ".join(hits) or "-", latency=latency,
            units=usage_of(resp), output=out_text[:160],
            ok=ok, advisory=advisory, note="; ".join(problems) or case.get("note", "-"),
        ))
    return results
