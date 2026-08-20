"""Bedrock Guardrails capability demo — single entry point.

Usage:
    python3 src/run_demo.py                 # provision + publish version + run everything
    python3 src/run_demo.py --no-publish     # stay on DRAFT
    python3 src/run_demo.py --skip-setup     # reuse existing demo guardrails (latest version)
    python3 src/run_demo.py --only A,C       # run selected phases (A,B,C,D,V,S,K)
    python3 src/run_demo.py --only S,K       # model-free suites: the two standalone APIs
    python3 src/run_demo.py --only R         # Automated Reasoning checks only
"""
import argparse
import sys

import cases as CS
import report
import runner_apply
import runner_ar
import runner_checks
import runner_converse
import runner_mantle
import runner_standalone
import runner_version
import setup_guardrail
from common import C, REGION, banner


def _latest_version(gid):
    return setup_guardrail.latest_version(gid) or "DRAFT"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-setup", action="store_true")
    ap.add_argument("--no-publish", action="store_true",
                    help="do not create a numbered version; run against DRAFT")
    ap.add_argument("--force-publish", action="store_true",
                    help="always cut a new numbered version instead of reusing the latest")
    ap.add_argument("--only", default="A,B,C,D,V,S,K,R",
                    help="comma list of phases: A,B,C,D,V,S,K,R (S,K,R never invoke a model)")
    args = ap.parse_args()
    phases = {p.strip().upper() for p in args.only.split(",") if p.strip()}

    print(f"{C.BOLD}Bedrock Guardrails capability demo{C.OFF}  region={REGION}")

    if args.skip_setup:
        ids = {}
        for key, name in (("standard", "demo-guardrail-standard"),
                          ("classic", "demo-guardrail-classic")):
            g = setup_guardrail.find_by_name(name)
            if not g:
                sys.exit(f"guardrail {name} not found; run without --skip-setup")
            ids[key] = g["id"]
            ids[f"{key}_version"] = ("DRAFT" if args.no_publish else _latest_version(g["id"]))
        print(f"  reusing guardrails: {ids}")
    else:
        ids = setup_guardrail.main(do_publish=not args.no_publish,
                                   force_publish=args.force_publish)

    std, std_v = ids["standard"], ids["standard_version"]
    cls, cls_v = ids["classic"], ids["classic_version"]
    print(f"\n  {C.BOLD}evaluating against:{C.OFF} standard={std} v{std_v}  classic={cls} v{cls_v}")

    results = []

    if "A" in phases:
        banner(f"PHASE A — ApplyGuardrail (Standard tier, version {std_v})")
        results += runner_apply.run(std, phase=f"A/ApplyGuardrail-Standard(v{std_v})",
                                   version=std_v)

        banner(f"PHASE A2 — Classic tier on the same cases (version {cls_v})")
        print(f"  {C.DIM}Classic tier officially covers English/French/Spanish only; Chinese "
              f"differences below are expected and marked DIFF, not FAIL.{C.OFF}")
        results += runner_apply.run(cls, phase=f"A2/ApplyGuardrail-Classic(v{cls_v})",
                                    case_ids=CS.TIER_COMPARE_IDS, advisory=True, version=cls_v)

    if "B" in phases:
        banner(f"PHASE B — Converse with inline guardrailConfig (version {std_v})")
        results += runner_converse.run(std, version=std_v)

    if "C" in phases:
        banner("PHASE C — Endpoint comparison: bedrock-runtime vs bedrock-mantle")
        results += runner_mantle.run(std, version=std_v)

    if "D" in phases:
        banner("PHASE D — InvokeGuardrailChecks (resource-less scoring mode)")
        results += runner_checks.run()

    if "V" in phases:
        banner(f"PHASE V — Version pinning: DRAFT moves, version {std_v} is frozen")
        results += runner_version.run(std, std_v)

    if "S" in phases or "K" in phases:
        api_gid = setup_guardrail.find_by_name("demo-guardrail-apitest")
        if not api_gid:
            api_gid = {"id": setup_guardrail.ensure("guardrail_apitest.json")}
        if "S" in phases:
            banner("PHASE S — ApplyGuardrail coverage suite (no model invoked)")
            results += runner_standalone.run_apply(api_gid["id"])
        if "K" in phases:
            banner("PHASE K — InvokeGuardrailChecks coverage suite (no model, no guardrail resource)")
            results += runner_standalone.run_checks()
        runner_standalone.coverage_summary(results)

    if "R" in phases:
        banner("PHASE R — Automated Reasoning checks (policy build + validation, no model)")
        results += runner_ar.run()

    banner("REPORT")
    failures = report.console(results)
    report.markdown(results, ids)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
