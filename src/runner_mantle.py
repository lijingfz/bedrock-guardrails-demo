"""Path C — endpoint comparison: does bedrock-mantle honour guardrails?

Sub-tests
  C1  bedrock-runtime /openai/v1/chat/completions + X-Amzn-Bedrock-Guardrail* headers
  C2  bedrock-mantle  /v1/chat/completions        + the same headers
  C3  the portable pattern for bedrock-mantle: ApplyGuardrail(INPUT) -> model -> ApplyGuardrail(OUTPUT)

The probes deliberately use *custom* policies (denied topic, custom blocked word) instead of
harmful content, so that a refusal produced by the model's own safety training cannot be
mistaken for a guardrail intervention.
"""
import cases as CS
from common import (C, MANTLE_BASE, OPENAI_MODEL_MANTLE, OPENAI_MODEL_RUNTIME,
                    RUNTIME_OPENAI_BASE, hit_policies, runtime, sigv4_post)
from runner_apply import evaluate

PROBES = ["topic_en", "word_custom_en"]


def _headers(gid, version):
    return {
        "X-Amzn-Bedrock-GuardrailIdentifier": gid,
        "X-Amzn-Bedrock-GuardrailVersion": version,
        "X-Amzn-Bedrock-Trace": "ENABLED",
    }


def _chat(base, model, prompt, headers):
    status, body = sigv4_post(
        f"{base}/chat/completions",
        {"model": model, "messages": [{"role": "user", "content": prompt}],
         "max_completion_tokens": 600},
        extra_headers=headers,
    )
    if isinstance(body, dict) and body.get("choices"):
        choice = body["choices"][0]
        msg = choice.get("message", {}) or {}
        # gpt-oss models may return the answer only in `reasoning` when truncated
        text = msg.get("content") or msg.get("reasoning") or ""
        finish = choice.get("finish_reason")
    else:
        text, finish = "", None
    return status, body, (text or "").strip(), finish


def _looks_guardrailed(text, body):
    return "[GUARDRAIL]" in text or "amazon-bedrock-guardrailAction" in str(body)[:4000]


def run(gid, version="DRAFT"):
    results = []

    for cid in PROBES:
        case = CS.by_id(cid)
        prompt = case["text"]

        # ---- C1: bedrock-runtime, OpenAI-compatible surface, guardrail headers ----
        status, body, text, finish = _chat(
            RUNTIME_OPENAI_BASE, OPENAI_MODEL_RUNTIME, prompt, _headers(gid, version))
        blocked = _looks_guardrailed(text, body)
        ok = status == 200 and blocked
        mark = f"{C.GREEN}PASS{C.OFF}" if ok else f"{C.RED}FAIL{C.OFF}"
        print(f"  [{mark}] C1 runtime+headers  {cid:<14} HTTP {status} blocked={blocked} "
              f"finish={finish}")
        print(f"         {C.DIM}reply: {(text or str(body))[:150]}{C.OFF}")
        results.append(dict(
            phase="C1/bedrock-runtime openai+guardrail-header", case=cid, desc=case["desc"],
            lang=case["lang"], source="INPUT", expect="blocked",
            actual=f"HTTP {status}, blocked={blocked}", hits="-", latency=None, units={},
            output=(text or str(body))[:160], ok=ok, advisory=False,
            note="-" if ok else "guardrail header not honoured on bedrock-runtime",
        ))

        # ---- C2: bedrock-mantle, same headers ----
        status, body, text, finish = _chat(
            MANTLE_BASE, OPENAI_MODEL_MANTLE, prompt, _headers(gid, version))
        blocked = _looks_guardrailed(text, body)
        enforced = status == 200 and blocked
        rejected = status >= 400
        # Documented behaviour: guardrails are not available on bedrock-mantle, so the
        # expected result is "not enforced" (request answered normally, or rejected).
        ok = not enforced
        mark = f"{C.GREEN}PASS{C.OFF}" if ok else f"{C.YELLOW}DIFF{C.OFF}"
        verdict = ("guardrail ENFORCED" if enforced else
                   f"header rejected (HTTP {status})" if rejected else
                   "header SILENTLY IGNORED - model answered normally")
        print(f"  [{mark}] C2 mantle+headers   {cid:<14} HTTP {status} -> {verdict}")
        print(f"         {C.DIM}reply: {(text or str(body))[:150]}{C.OFF}")
        results.append(dict(
            phase="C2/bedrock-mantle openai+guardrail-header", case=cid, desc=case["desc"],
            lang=case["lang"], source="INPUT", expect="not enforced (per docs)",
            actual=verdict, hits="-", latency=None, units={},
            output=(text or str(body))[:160], ok=ok, advisory=False, note=verdict,
        ))

    # ---- C3: sidecar pattern on bedrock-mantle ----
    print(f"\n  {C.BOLD}C3 sidecar pattern on bedrock-mantle "
          f"(ApplyGuardrail INPUT -> model -> ApplyGuardrail OUTPUT){C.OFF}")

    # C3a: input blocked before the model is ever called
    case = CS.by_id("topic_en")
    resp, hits, latency = evaluate(gid, case, version)
    ok = resp["action"] == "GUARDRAIL_INTERVENED"
    called_model = not ok
    mark = f"{C.GREEN}PASS{C.OFF}" if ok else f"{C.RED}FAIL{C.OFF}"
    print(f"  [{mark}] C3a pre-check blocks input, model never invoked "
          f"(action={resp['action']}, model_called={called_model})")
    results.append(dict(
        phase="C3/mantle sidecar", case="pre_check_block", desc="Pre-check blocks denied topic",
        lang="en", source="INPUT", expect="GUARDRAIL_INTERVENED", actual=resp["action"],
        hits=", ".join(hits) or "-", latency=latency, units={},
        output=" | ".join(o.get("text", "") for o in resp.get("outputs", []))[:160],
        ok=ok, advisory=False, note="model not invoked" if ok else "input not blocked",
    ))

    # C3b: input passes, model output carries PII, post-check masks it
    prompt = ("Reply with exactly this sentence and nothing else: "
              "You can reach the customer at alice.wong@example.com or +1 425 555 0199.")
    pre, pre_hits, _ = evaluate(gid, dict(source="INPUT", text=prompt), version)
    status, body, text, finish = _chat(MANTLE_BASE, OPENAI_MODEL_MANTLE, prompt, None)
    post = runtime.apply_guardrail(
        guardrailIdentifier=gid, guardrailVersion=version, source="OUTPUT",
        content=[{"text": {"text": text or "(empty)"}}], outputScope="FULL")
    post_hits = hit_policies(post.get("assessments", []))
    masked = " | ".join(o.get("text", "") for o in post.get("outputs", []))
    ok = post["action"] == "GUARDRAIL_INTERVENED" and "{EMAIL}" in masked
    mark = f"{C.GREEN}PASS{C.OFF}" if ok else f"{C.RED}FAIL{C.OFF}"
    print(f"  [{mark}] C3b pre-check={pre['action']}  mantle HTTP {status}  "
          f"post-check={post['action']}")
    print(f"         {C.DIM}raw model output : {text[:120]}{C.OFF}")
    print(f"         {C.DIM}after post-check : {masked[:120]}{C.OFF}")
    results.append(dict(
        phase="C3/mantle sidecar", case="post_check_mask",
        desc="Input passes, model output PII masked by post-check", lang="en", source="OUTPUT",
        expect="GUARDRAIL_INTERVENED + {EMAIL}", actual=post["action"],
        hits=", ".join(post_hits) or "-", latency=None, units={}, output=masked[:160],
        ok=ok, advisory=False, note="-" if ok else "post-check did not mask PII",
    ))
    return results
