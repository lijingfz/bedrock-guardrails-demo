"""Shared helpers: clients, config, console formatting, SigV4 HTTP calls."""
import json
import os
import pathlib

import boto3
import botocore.auth
import botocore.awsrequest
import urllib3

REGION = os.environ.get("DEMO_REGION", "us-east-1")
ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
RESULTS_DIR = ROOT / "results"

# Model used for the Converse path (Bedrock-native, guardrail attached inline).
CONVERSE_MODEL = os.environ.get("DEMO_CONVERSE_MODEL", "amazon.nova-lite-v1:0")
# Model available on BOTH bedrock-runtime and bedrock-mantle, for endpoint comparison.
OPENAI_MODEL_RUNTIME = os.environ.get("DEMO_OAI_MODEL_RUNTIME", "openai.gpt-oss-20b-1:0")
OPENAI_MODEL_MANTLE = os.environ.get("DEMO_OAI_MODEL_MANTLE", "openai.gpt-oss-20b")

RUNTIME_OPENAI_BASE = f"https://bedrock-runtime.{REGION}.amazonaws.com/openai/v1"
MANTLE_BASE = f"https://bedrock-mantle.{REGION}.api.aws/v1"

_session = boto3.Session(region_name=REGION)
bedrock = _session.client("bedrock", region_name=REGION)
runtime = _session.client("bedrock-runtime", region_name=REGION)
_http = urllib3.PoolManager()


class C:
    """ANSI colors, disabled when not a tty."""

    _on = os.isatty(1) and os.environ.get("NO_COLOR") is None
    GREEN = "\033[32m" if _on else ""
    RED = "\033[31m" if _on else ""
    YELLOW = "\033[33m" if _on else ""
    CYAN = "\033[36m" if _on else ""
    DIM = "\033[2m" if _on else ""
    BOLD = "\033[1m" if _on else ""
    OFF = "\033[0m" if _on else ""


def banner(text):
    print(f"\n{C.BOLD}{C.CYAN}{'=' * 78}\n{text}\n{'=' * 78}{C.OFF}")


def load_config(name):
    with open(CONFIG_DIR / name, encoding="utf-8") as fh:
        return json.load(fh)


def sigv4_post(url, payload, extra_headers=None):
    """POST JSON to an AWS endpoint signed with SigV4 (service 'bedrock')."""
    body = json.dumps(payload)
    headers = {"Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    req = botocore.awsrequest.AWSRequest(method="POST", url=url, data=body, headers=headers)
    creds = _session.get_credentials().get_frozen_credentials()
    botocore.auth.SigV4Auth(creds, "bedrock", REGION).add_auth(req)
    resp = _http.request("POST", url, body=body.encode("utf-8"), headers=dict(req.headers))
    try:
        parsed = json.loads(resp.data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        parsed = {"_raw": resp.data[:2000].decode("utf-8", "replace")}
    return resp.status, parsed


def hit_policies(assessments):
    """Flatten ApplyGuardrail/trace assessments into a sorted set of 'policy.detail' hits."""
    hits = set()
    for a in assessments or []:
        for t in a.get("topicPolicy", {}).get("topics", []):
            if t.get("detected", t.get("action") == "BLOCKED"):
                hits.add(f"topicPolicy.{t['name']}")
        for f in a.get("contentPolicy", {}).get("filters", []):
            if f.get("detected", f.get("action") == "BLOCKED"):
                hits.add(f"contentPolicy.{f['type']}")
        wp = a.get("wordPolicy", {})
        for w in wp.get("customWords", []):
            if w.get("detected", True):
                hits.add("wordPolicy.CUSTOM")
        for w in wp.get("managedWordLists", []):
            if w.get("detected", True):
                hits.add(f"wordPolicy.{w.get('type', 'MANAGED')}")
        sp = a.get("sensitiveInformationPolicy", {})
        for p in sp.get("piiEntities", []):
            if p.get("detected", True):
                hits.add(f"pii.{p['type']}.{p['action']}")
        for r in sp.get("regexes", []):
            if r.get("detected", True):
                hits.add(f"regex.{r['name']}.{r['action']}")
        for f in a.get("contextualGroundingPolicy", {}).get("filters", []):
            if f.get("detected", f.get("action") == "BLOCKED"):
                hits.add(f"grounding.{f['type']}")
    return sorted(hits)


def usage_of(resp):
    u = resp.get("usage") or {}
    return {k: v for k, v in u.items() if v}
