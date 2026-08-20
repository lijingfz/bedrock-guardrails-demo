"""Phase R — Automated Reasoning checks (the 6th guardrail policy type).

No foundation model is invoked: the "model response" under validation is supplied as plain
text to ApplyGuardrail, which is also the integration path AWS recommends for AR checks.

Pipeline (idempotent, ~2 minutes on a cold account):
    CreateAutomatedReasoningPolicy
        -> StartAutomatedReasoningPolicyBuildWorkflow(INGEST_CONTENT, rules document)
        -> poll GetAutomatedReasoningPolicyBuildWorkflow until COMPLETED
        -> GetAutomatedReasoningPolicyBuildWorkflowResultAssets(POLICY_DEFINITION)
        -> UpdateAutomatedReasoningPolicy   <-- the build does NOT write the draft for you
        -> CreateAutomatedReasoningPolicyVersion
        -> attach the version ARN to a guardrail via automatedReasoningPolicyConfig
        -> ApplyGuardrail(source=OUTPUT) and assert the finding types
"""
import time

from common import C, CONFIG_DIR, bedrock, load_config, runtime
from runner_standalone import record
from setup_guardrail import _wait_ready, find_by_name

POLICY_NAME = "demo-ar-expense-policy"
SOURCE_DOC = "ar_policy_source.txt"
GUARDRAIL_CONFIG = "guardrail_ar.json"

FINDING_TYPES = ("valid", "invalid", "satisfiable", "impossible",
                 "translationAmbiguous", "tooComplex", "noTranslations")
# worst -> best, per the AWS integration guide
SEVERITY = {"tooComplex": 0, "translationAmbiguous": 0, "impossible": 1,
            "invalid": 2, "satisfiable": 3, "valid": 4, "noTranslations": 5}


def finding_type(finding):
    for key in FINDING_TYPES:
        if key in finding:
            return key, finding[key]
    return None, None


def aggregate(findings):
    """Worst finding wins — the decision helper an application would implement."""
    worst, worst_sev = None, float("inf")
    for f in findings:
        ftype, _ = finding_type(f)
        sev = SEVERITY.get(ftype, 0)
        if sev < worst_sev:
            worst, worst_sev = ftype, sev
    return worst


# ------------------------------------------------------------------ provisioning
def _policy_arn():
    for p in bedrock.list_automated_reasoning_policies().get("automatedReasoningPolicySummaries", []):
        if p["name"] == POLICY_NAME:
            return p["policyArn"]
    return None


def _rules_in(version_arn):
    pd = bedrock.export_automated_reasoning_policy_version(policyArn=version_arn)["policyDefinition"]
    return len(pd.get("rules", [])), len(pd.get("variables", []))


def _latest_usable_version(arn):
    """Newest numbered version whose definition actually contains rules."""
    best = None
    for p in bedrock.list_automated_reasoning_policies(policyArn=arn).get(
            "automatedReasoningPolicySummaries", []):
        v = p.get("version")
        if v and v.isdigit():
            rules, _ = _rules_in(f"{arn}:{v}")
            if rules and (best is None or int(v) > int(best)):
                best = v
    return best


def ensure_policy(timeout_s=900):
    arn = _policy_arn()
    if arn:
        version = _latest_usable_version(arn)
        if version:
            rules, variables = _rules_in(f"{arn}:{version}")
            print(f"  {C.CYAN}reusing{C.OFF} AR policy {arn.split('/')[-1]} version {version} "
                  f"({rules} rules, {variables} variables)")
            return arn, version
    if not arn:
        arn = bedrock.create_automated_reasoning_policy(
            name=POLICY_NAME,
            description="Demo travel expense reimbursement rules")["policyArn"]
        print(f"  {C.GREEN}created{C.OFF} AR policy {arn.split('/')[-1]}")

    doc = (CONFIG_DIR / SOURCE_DOC).read_bytes()
    wf = bedrock.start_automated_reasoning_policy_build_workflow(
        policyArn=arn, buildWorkflowType="INGEST_CONTENT",
        sourceContent={"workflowContent": {"documents": [{
            "document": doc, "documentContentType": "txt",
            "documentName": "travel-expense-policy",
            "documentDescription": "Demo reimbursement rules"}]}})["buildWorkflowId"]
    print(f"  build workflow {wf} (INGEST_CONTENT) ", end="", flush=True)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        status = bedrock.get_automated_reasoning_policy_build_workflow(
            policyArn=arn, buildWorkflowId=wf)["status"]
        if status == "COMPLETED":
            print(f" {C.GREEN}COMPLETED{C.OFF}")
            break
        if status in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"AR build workflow {status}")
        print(".", end="", flush=True)
        time.sleep(10)
    else:
        raise RuntimeError("AR build workflow timed out")

    built = bedrock.get_automated_reasoning_policy_build_workflow_result_assets(
        policyArn=arn, buildWorkflowId=wf,
        assetType="POLICY_DEFINITION")["buildWorkflowAssets"]["policyDefinition"]
    print(f"  extracted {len(built.get('types', []))} types, {len(built.get('variables', []))} "
          f"variables, {len(built.get('rules', []))} rules from the source document")
    bedrock.update_automated_reasoning_policy(
        policyArn=arn, policyDefinition=built,
        description="Demo travel expense reimbursement rules (built from source doc)")
    digest = bedrock.get_automated_reasoning_policy(policyArn=arn)["definitionHash"]
    version = bedrock.create_automated_reasoning_policy_version(
        policyArn=arn, lastUpdatedDefinitionHash=digest)["version"]
    print(f"  {C.GREEN}published{C.OFF} AR policy version {C.BOLD}{version}{C.OFF}")
    return arn, version


def ensure_guardrail(version_arn):
    cfg = load_config(GUARDRAIL_CONFIG)
    params = {k: v for k, v in cfg.items() if k != "tags"}
    params["automatedReasoningPolicyConfig"] = {"policies": [version_arn]}
    existing = find_by_name(cfg["name"])
    if existing:
        params["guardrailIdentifier"] = existing["id"]
        bedrock.update_guardrail(**params)
        gid = existing["id"]
    else:
        params["tags"] = cfg["tags"]
        gid = bedrock.create_guardrail(**params)["guardrailId"]
    _wait_ready(gid)
    print(f"  guardrail {cfg['name']} ({gid}) carries {version_arn.split('/')[-1]}")
    return gid


# ------------------------------------------------------------------------ tests
def _validate(gid, text, qualifiers=None):
    inner = {"text": text}
    if qualifiers:
        inner["qualifiers"] = qualifiers
    resp = runtime.apply_guardrail(guardrailIdentifier=gid, guardrailVersion="DRAFT",
                                   source="OUTPUT", content=[{"text": inner}], outputScope="FULL")
    ar = (resp.get("assessments") or [{}])[0].get("automatedReasoningPolicy", {})
    return resp, ar.get("findings", [])


def run(gid=None, policy_version_arn=None):
    results = []
    arn, version = ensure_policy()
    version_arn = policy_version_arn or f"{arn}:{version}"
    ar_gid = gid or ensure_guardrail(version_arn)

    def case(cid, desc, text, want, need_keys=(), accept=(), attempts=2):
        # AR translation is not fully deterministic on borderline phrasings, so a case may
        # be retried; the expectation itself is never relaxed.
        allowed = {want, *accept}
        for attempt in range(attempts):
            resp, findings = _validate(ar_gid, text)
            worst = aggregate(findings)
            if worst in allowed:
                break
        got = [finding_type(f)[0] for f in findings]
        problems = []
        if worst not in allowed:
            problems.append(f"aggregate={worst} want one of {sorted(allowed)}")
        data = dict(findings[0]).get(worst) if findings else None
        for key in need_keys:
            if not (isinstance(data, dict) and data.get(key)):
                problems.append(f"{worst}.{key} missing/empty")
        if resp["action"] != "NONE":
            problems.append(f"AR is detect-only but action={resp['action']}")
        if resp["usage"].get("automatedReasoningPolicyUnits", 0) < 1:
            problems.append("automatedReasoningPolicyUnits=0 (checks did not run)")
        if resp["usage"].get("automatedReasoningPolicies") != 1:
            problems.append(f"automatedReasoningPolicies={resp['usage'].get('automatedReasoningPolicies')}")
        return record("R/AutomatedReasoning", cid, desc, want, ",".join(got) or "none",
                      problems, usage={k: v for k, v in resp["usage"].items() if v})

    print(f"  {C.DIM}--- finding types ---{C.OFF}")
    results.append(case("R01_valid_late_receipt",
                        "Claim provable from a rule -> valid + supportingRules",
                        "User: I submitted the receipt 45 days after the expense date.\n"
                        "Assistant: That expense is not reimbursable.",
                        "valid", need_keys=("supportingRules", "translation")))
    results.append(case("R02_valid_contractor",
                        "Eligibility rule proves the answer -> valid",
                        "User: I am a contractor.\n"
                        "Assistant: You are not eligible to submit an expense claim.",
                        "valid", need_keys=("supportingRules",)))
    results.append(case("R03_valid_approval",
                        "Threshold rule proves the answer -> valid",
                        "User: My total claim amount is 1500 dollars.\n"
                        "Assistant: This claim requires manager approval.",
                        "valid", need_keys=("supportingRules",)))
    results.append(case("R04_invalid_hotel_cap",
                        "Answer contradicts the 250/night cap -> invalid + contradictingRules",
                        "User: I am a full-time employee. My hotel room rate was 400 dollars "
                        "per night.\nAssistant: The hotel expense is reimbursable.",
                        "invalid", need_keys=("contradictingRules", "translation")))
    results.append(case("R05_satisfiable_business_class",
                        "Answer true only under unstated conditions -> satisfiable",
                        "User: I flew business class.\n"
                        "Assistant: Business class airfare is reimbursable.",
                        "satisfiable", need_keys=("claimsTrueScenario", "claimsFalseScenario")))
    results.append(case("R06_no_translations",
                        "Off-topic content -> noTranslations (nothing to verify)",
                        "Assistant: The capital of France is Paris.",
                        "noTranslations"))
    results.append(case("R07_ambiguous_or_invalid",
                        "Ambiguous phrasing -> translationAmbiguous (or invalid)",
                        "User: I am a full-time employee. My meal cost 120 dollars for the day "
                        "and I submitted the receipt after 2 days.\n"
                        "Assistant: The meal expense is reimbursable.",
                        "translationAmbiguous", accept=("invalid",)))

    print(f"  {C.DIM}--- rule provenance, detect-mode, error path, negative control ---{C.OFF}")
    resp, findings = _validate(ar_gid, "User: I submitted the receipt 45 days after the expense "
                                       "date.\nAssistant: That expense is reimbursable.")
    rules = []
    for f in findings:
        _, data = finding_type(f)
        if isinstance(data, dict):
            rules += data.get("contradictingRules", []) + data.get("supportingRules", [])
    problems = []
    if not rules:
        problems.append("no rule references returned")
    elif not all(r.get("identifier") and r.get("policyVersionArn") for r in rules):
        problems.append(f"rule refs missing identifier/policyVersionArn: {rules[:2]}")
    elif not all(r["policyVersionArn"].endswith(f":{version}") for r in rules):
        problems.append(f"rule refs point at another policy version: {rules[0]}")
    results.append(record("R/AutomatedReasoning", "R08_rule_provenance",
                          "Findings cite rule id + the exact policy version ARN",
                          f"identifier + ...:{version}",
                          rules[0].get("identifier") if rules else "none", problems))

    rules_n, vars_n = _rules_in(version_arn)
    problems = [] if rules_n > 0 and vars_n > 0 else [f"published version has {rules_n} rules"]
    results.append(record("R/AutomatedReasoning", "R09_version_has_rules",
                          "Published policy version really contains the extracted logic",
                          "rules>0 and variables>0", f"{rules_n} rules / {vars_n} variables",
                          problems))

    try:
        _validate(ar_gid, "Is my 60 dollar meal reimbursable?", qualifiers=["query"])
        got, problems = "no error", ["expected ValidationException"]
    except Exception as exc:  # noqa: BLE001
        got = type(exc).__name__
        problems = [] if "ValidationException" in got else [f"got {got}"]
    results.append(record("R/AutomatedReasoning", "R10_err_no_claim",
                          "query-only content has no claim -> ValidationException",
                          "ValidationException", got, problems))

    plain = find_by_name("demo-guardrail-standard")
    if plain:
        resp, findings = _validate(plain["id"],
                                   "User: My hotel was 400 per night.\nAssistant: It is reimbursable.")
        units = resp["usage"].get("automatedReasoningPolicyUnits", 0)
        problems = [] if units == 0 and not findings else [
            f"guardrail without an AR policy returned units={units}, findings={len(findings)}"]
        results.append(record("R/AutomatedReasoning", "R11_silent_skip_control",
                              "Guardrail without an AR policy: units=0 and no findings, no error",
                              "units=0, findings=0", f"units={units}, findings={len(findings)}",
                              problems))

    worst = aggregate([{"valid": {}}, {"invalid": {}}, {"satisfiable": {}}])
    problems = [] if worst == "invalid" else [f"aggregate picked {worst}"]
    results.append(record("R/AutomatedReasoning", "R12_aggregate_severity",
                          "Worst-finding aggregation (valid+invalid+satisfiable -> invalid)",
                          "invalid", worst, problems))
    return results
