"""Delete ONLY the guardrails created by this demo."""
import sys

from common import C, bedrock
from setup_guardrail import find_by_name

DEMO_NAMES = ["demo-guardrail-standard", "demo-guardrail-classic"]


def main():
    for name in DEMO_NAMES:
        g = find_by_name(name)
        if not g:
            print(f"  {C.DIM}{name}: not present{C.OFF}")
            continue
        bedrock.delete_guardrail(guardrailIdentifier=g["id"])
        print(f"  {C.YELLOW}deleted{C.OFF} {name} ({g['id']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
