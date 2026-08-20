"""Phase V — version pinning: prove a published version is immutable while DRAFT keeps moving.

Steps
  1. add a temporary blocked word to the DRAFT of the standard guardrail
  2. evaluate the same text against DRAFT   -> expected BLOCK   (DRAFT sees the change)
  3. evaluate the same text against version -> expected NONE    (published version is frozen)
  4. restore the DRAFT to the on-disk configuration

This is why production applications should pin a numbered version instead of DRAFT.
"""
import time

from common import C, bedrock, hit_policies, load_config, runtime

TEMP_WORD = "TempWordZeta"
PROBE = f"Please give me a status update on {TEMP_WORD} before the meeting."


def _update_draft(gid, extra_word=None):
    cfg = load_config("guardrail_standard.json")
    params = {k: v for k, v in cfg.items() if k != "tags"}
    if extra_word:
        params["wordPolicyConfig"]["wordsConfig"].append({"text": extra_word})
    params["guardrailIdentifier"] = gid
    bedrock.update_guardrail(**params)
    for _ in range(30):
        if bedrock.get_guardrail(guardrailIdentifier=gid)["status"] == "READY":
            return
        time.sleep(2)
    raise RuntimeError("DRAFT not READY after update")


def _eval(gid, version):
    resp = runtime.apply_guardrail(
        guardrailIdentifier=gid, guardrailVersion=version, source="INPUT",
        content=[{"text": {"text": PROBE}}], outputScope="FULL")
    return resp["action"], hit_policies(resp.get("assessments", []))


def run(gid, version):
    results = []
    if version == "DRAFT":
        print(f"  {C.YELLOW}skipped{C.OFF} — no numbered version published (running on DRAFT)")
        return results

    print(f"  {C.DIM}adding temporary blocked word '{TEMP_WORD}' to DRAFT only...{C.OFF}")
    _update_draft(gid, extra_word=TEMP_WORD)
    try:
        draft_action, draft_hits = _eval(gid, "DRAFT")
        ver_action, ver_hits = _eval(gid, version)
    finally:
        _update_draft(gid)  # restore
        print(f"  {C.DIM}DRAFT restored to config/guardrail_standard.json{C.OFF}")

    for label, want, action, hits, expect_txt in (
        ("draft_sees_change", "GUARDRAIL_INTERVENED", draft_action, draft_hits,
         "DRAFT picks up the new word"),
        (f"version_{version}_frozen", "NONE", ver_action, ver_hits,
         f"version {version} unaffected by DRAFT edits"),
    ):
        ok = action == want
        mark = f"{C.GREEN}PASS{C.OFF}" if ok else f"{C.RED}FAIL{C.OFF}"
        print(f"  [{mark}] {label:<22} action={action:<22} hits={', '.join(hits) or '-'}")
        results.append(dict(
            phase="V/version-pinning", case=label, desc=expect_txt, lang="en", source="INPUT",
            expect=want, actual=action, hits=", ".join(hits) or "-", latency=None, units={},
            output="-", ok=ok, advisory=False,
            note=expect_txt if ok else f"expected {want}",
        ))
    return results
