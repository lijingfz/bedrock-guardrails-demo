"""Delete ONLY the guardrails created by this demo."""
import sys

from common import C, bedrock
from setup_guardrail import find_by_name

DEMO_NAMES = ["demo-guardrail-standard", "demo-guardrail-classic",
              "demo-guardrail-apitest", "demo-guardrail-ar"]
AR_POLICY_NAME = "demo-ar-expense-policy"


def main():
    for name in DEMO_NAMES:
        g = find_by_name(name)
        if not g:
            print(f"  {C.DIM}{name}: not present{C.OFF}")
            continue
        bedrock.delete_guardrail(guardrailIdentifier=g["id"])
        print(f"  {C.YELLOW}deleted{C.OFF} {name} ({g['id']})")

    for p in bedrock.list_automated_reasoning_policies().get(
            "automatedReasoningPolicySummaries", []):
        if p["name"] == AR_POLICY_NAME:
            bedrock.delete_automated_reasoning_policy(policyArn=p["policyArn"])
            print(f"  {C.YELLOW}deleted{C.OFF} AR policy {AR_POLICY_NAME} "
                  f"({p['policyArn'].split('/')[-1]})")
            break
    else:
        print(f"  {C.DIM}{AR_POLICY_NAME}: not present{C.OFF}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
