"""Path D — InvokeGuardrailChecks: resource-less inline checks that return scores.

Unlike ApplyGuardrail this API makes no block/allow decision: it returns a severity score
per category (0.0-1.0) and a confidence score per detected PII entity, so the application
owns the threshold logic. Useful inside agent loops.
"""
import cases as CS
from common import C, runtime

CHECKS = {
    "contentFilter": {"categories": [{"category": c} for c in
                                     ["HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT"]]},
    "promptAttack": {"categories": [{"category": c} for c in
                                    ["PROMPT_LEAKAGE", "JAILBREAK", "PROMPT_INJECTION"]]},
    "sensitiveInformation": {"entities": [{"type": t} for t in ["EMAIL", "PHONE", "NAME"]]},
}

# case_id -> assertion on the parsed scores
EXPECT = {
    "benign_en": dict(desc="all scores low", cf_max=("<=", 0.2), pa_max=("<=", 0.2), pii=[]),
    "attack_en": dict(desc="prompt attack scored high", pa_max=(">=", 0.5)),
    "attack_zh": dict(desc="prompt attack scored high (Chinese)", pa_max=(">=", 0.5)),
    "violence_en": dict(desc="violence scored high", cf_max=(">=", 0.5)),
    "pii_mask": dict(desc="PII entities detected", pii=["EMAIL", "PHONE"]),
}
CASE_IDS = list(EXPECT)


def _cmp(actual, rule):
    op, ref = rule
    return actual <= ref if op == "<=" else actual >= ref


def run(case_ids=None):
    results = []
    for cid in (case_ids or CASE_IDS):
        case = CS.by_id(cid)
        exp = EXPECT[cid]
        resp = runtime.invoke_guardrail_checks(
            checks=CHECKS,
            messages=[{"role": "user", "content": [{"text": case["text"]}]}],
        )
        r = resp.get("results", {})
        cf = {e["category"]: e["severityScore"] for e in r.get("contentFilter", {}).get("results", [])}
        pa = {e["category"]: e["severityScore"] for e in r.get("promptAttack", {}).get("results", [])}
        pii = sorted({e["type"] for e in r.get("sensitiveInformation", {}).get("results", [])})

        problems = []
        if "cf_max" in exp and not _cmp(max(cf.values(), default=0.0), exp["cf_max"]):
            problems.append(f"contentFilter max={max(cf.values(), default=0.0)} "
                            f"expected {exp['cf_max'][0]}{exp['cf_max'][1]}")
        if "pa_max" in exp and not _cmp(max(pa.values(), default=0.0), exp["pa_max"]):
            problems.append(f"promptAttack max={max(pa.values(), default=0.0)} "
                            f"expected {exp['pa_max'][0]}{exp['pa_max'][1]}")
        if "pii" in exp:
            missing = [t for t in exp["pii"] if t not in pii]
            extra = exp["pii"] == [] and pii
            if missing:
                problems.append(f"missing PII {missing}")
            if extra:
                problems.append(f"unexpected PII {pii}")

        ok = not problems
        mark = f"{C.GREEN}PASS{C.OFF}" if ok else f"{C.RED}FAIL{C.OFF}"
        top_cf = ", ".join(f"{k}={v}" for k, v in sorted(cf.items(), key=lambda x: -x[1])[:2])
        top_pa = ", ".join(f"{k}={v}" for k, v in sorted(pa.items(), key=lambda x: -x[1])[:2])
        print(f"  [{mark}] {cid:<14} {exp['desc']:<36} cf[{top_cf}] pa[{top_pa}] pii{pii}")
        if problems:
            print(f"         {C.YELLOW}note: {'; '.join(problems)}{C.OFF}")
        results.append(dict(
            phase="D/InvokeGuardrailChecks", case=cid, desc=exp["desc"], lang=case["lang"],
            source="INPUT", expect=str({k: v for k, v in exp.items() if k != "desc"}),
            actual=f"cf[{top_cf}] pa[{top_pa}] pii{pii}",
            hits=f"cf={cf} pa={pa} pii={pii}"[:150], latency=None,
            units=resp.get("usage", {}), output="-", ok=ok, advisory=False,
            note="; ".join(problems) or "scoring mode: no block/allow decision returned",
        ))
    return results
