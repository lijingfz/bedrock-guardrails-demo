"""Path B — guardrail attached inline to a Bedrock-native model invocation (Converse)."""
import time

import cases as CS
from common import C, CONVERSE_MODEL, hit_policies, runtime


def run(gid, phase="B/Converse", case_ids=None, version="DRAFT"):
    selected = [CS.by_id(i) for i in (case_ids or CS.CONVERSE_IDS)]
    results = []
    for case in selected:
        t0 = time.time()
        resp = runtime.converse(
            modelId=CONVERSE_MODEL,
            messages=[{"role": "user", "content": [{"text": case["text"]}]}],
            guardrailConfig={
                "guardrailIdentifier": gid,
                "guardrailVersion": version,
                "trace": "enabled_full",
            },
            inferenceConfig={"maxTokens": 200, "temperature": 0},
        )
        wall_ms = int((time.time() - t0) * 1000)
        stop = resp.get("stopReason")
        text = " ".join(
            b.get("text", "") for b in resp["output"]["message"]["content"] if "text" in b
        ).strip()
        trace = resp.get("trace", {}).get("guardrail", {})
        assessments = list(trace.get("inputAssessment", {}).values()) + [
            a for lst in trace.get("outputAssessments", {}).values() for a in lst
        ]
        hits = hit_policies(assessments)

        expect_blocked = case["expect_action"] == "GUARDRAIL_INTERVENED"
        blocked = stop == "guardrail_intervened"
        problems = []
        if blocked != expect_blocked:
            problems.append(f"stopReason={stop} expected_blocked={expect_blocked}")
        for want in case.get("expect_hits", []):
            if not any(want in h for h in hits):
                problems.append(f"missing hit '{want}'")
        ok = not problems
        mark = f"{C.GREEN}PASS{C.OFF}" if ok else f"{C.RED}FAIL{C.OFF}"
        print(f"  [{mark}] {case['id']:<14} {case['desc'][:44]:<46} stopReason={stop} {wall_ms}ms")
        print(f"         {C.DIM}model reply: {text[:110]}{C.OFF}")
        if hits:
            print(f"         {C.DIM}hits: {', '.join(hits)}{C.OFF}")
        if problems:
            print(f"         {C.YELLOW}note: {'; '.join(problems)}{C.OFF}")
        results.append(dict(
            phase=phase, case=case["id"], desc=case["desc"], lang=case["lang"],
            source="INPUT", expect="blocked" if expect_blocked else "allowed",
            actual=str(stop), hits=", ".join(hits) or "-", latency=wall_ms,
            units={}, output=text[:160], ok=ok, advisory=False,
            note="; ".join(problems) or "-",
        ))
    return results
