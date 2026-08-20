"""Create (or idempotently update) the demo guardrails, and optionally publish a version."""
import sys
import time

from common import C, bedrock, banner, load_config


def find_by_name(name):
    paginator = bedrock.get_paginator("list_guardrails")
    for page in paginator.paginate():
        for g in page["guardrails"]:
            if g["name"] == name:
                return g
    return None


def _wait_ready(gid, version=None, tries=30):
    kwargs = {"guardrailIdentifier": gid}
    if version:
        kwargs["guardrailVersion"] = version
    for _ in range(tries):
        detail = bedrock.get_guardrail(**kwargs)
        if detail["status"] == "READY":
            return detail
        if detail["status"] == "FAILED":
            raise RuntimeError(f"guardrail {gid} v{version or 'DRAFT'} FAILED: "
                               f"{detail.get('statusReasons')}")
        time.sleep(2)
    raise RuntimeError(f"guardrail {gid} v{version or 'DRAFT'} not READY: {detail['status']}")


def ensure(config_file):
    cfg = load_config(config_file)
    name = cfg["name"]
    existing = find_by_name(name)
    if existing:
        gid = existing["id"]
        params = {k: v for k, v in cfg.items() if k != "tags"}
        params["guardrailIdentifier"] = gid
        bedrock.update_guardrail(**params)
        action = "updated"
    else:
        resp = bedrock.create_guardrail(**cfg)
        gid = resp["guardrailId"]
        action = "created"

    detail = _wait_ready(gid)
    tier = (
        detail.get("contentPolicy", {}).get("tier", {}).get("tierName")
        or cfg.get("contentPolicyConfig", {}).get("tierConfig", {}).get("tierName")
    )
    print(
        f"  {C.GREEN}{action}{C.OFF} {name}  id={gid}  tier={tier}  "
        f"crossRegion={detail.get('crossRegionDetails', {}).get('guardrailProfileId', '-')}"
    )
    return gid


def publish(gid, description="Published by guardrails capability demo"):
    """Promote the current DRAFT into an immutable numbered version."""
    resp = bedrock.create_guardrail_version(guardrailIdentifier=gid, description=description)
    version = resp["version"]
    _wait_ready(gid, version)
    print(f"  {C.GREEN}published{C.OFF} {gid} DRAFT -> version {C.BOLD}{version}{C.OFF} (immutable)")
    return version


def list_versions(gid):
    versions = []
    paginator = bedrock.get_paginator("list_guardrails")
    for page in paginator.paginate(guardrailIdentifier=gid):
        for g in page["guardrails"]:
            versions.append(g["version"])
    return versions


def latest_version(gid):
    numeric = [v for v in list_versions(gid) if v.isdigit()]
    return max(numeric, key=int) if numeric else None


def main(do_publish=True, force_publish=False):
    banner("PHASE 0 — Provision demo guardrails")
    ids = {
        "standard": ensure("guardrail_standard.json"),
        "classic": ensure("guardrail_classic.json"),
    }
    if do_publish:
        print(f"\n  {C.BOLD}Publishing DRAFT to a numbered version{C.OFF}")
        for key in ("standard", "classic"):
            gid = ids[key]
            current = latest_version(gid)
            if current and not force_publish:
                print(f"  {C.CYAN}reusing{C.OFF} {gid} version {C.BOLD}{current}{C.OFF} "
                      f"(use --force-publish to cut a new one)")
                ids[f"{key}_version"] = current
            else:
                ids[f"{key}_version"] = publish(gid)
            print(f"         {C.DIM}versions of {gid}: {', '.join(list_versions(gid))}{C.OFF}")
    else:
        ids["standard_version"] = ids["classic_version"] = "DRAFT"
    return ids


if __name__ == "__main__":
    print(main())
    sys.exit(0)
