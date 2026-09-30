"""Plain-language summary of flags that the deterministic rules already raised.

The LLM (a local LM Studio server, so MIMIC data never leaves the machine) only rewords the flag
list it is given. It never sees raw rows, never adds or clears a flag, and is skipped entirely when
there is nothing to explain.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

LMSTUDIO_URL = os.environ.get("CURIE_LLM_URL", "http://127.0.0.1:1234")
LMSTUDIO_MODEL = os.environ.get("CURIE_LLM_MODEL", "")  # empty: first model the server lists

SYSTEM = (
    "You summarize infusion safety flags for an ICU nurse. The user message is the complete JSON list "
    "of flags raised by a deterministic rule engine. Write exactly one bullet line per flag, in the "
    "given order, and nothing else: no headings, no preamble, no empty categories. Start each bullet "
    "with the flag's `action` text exactly, then the drug, then the reason in plain words using the "
    "exact values from `evidence`, then the rule_id in square brackets. Never add a drug, number, "
    "threshold, or advice that is not in the JSON, and never call a 'Cannot check' item safe.\n"
    "Format: - <action> <drug>: <reason with the evidence values> [<rule_id>]"
)
ORDER = {"do_not_infuse": 0, "review": 1, "insufficient_context": 2}
NUMBER = re.compile(r"\d+(?:\.\d+)?")


def action(flag: dict) -> str:
    if flag["status"] == "do_not_infuse":
        return "Do not start" if flag["hypothetical"] else "Hold"
    return {"review": "Review", "insufficient_context": "Cannot check"}[flag["status"]]


def _compact(flags: list[dict]) -> list[dict]:
    return [
        {"action": action(f), **{k: f[k] for k in ("rule_id", "drug", "message", "evidence")}}
        for f in flags
    ]


def _sorted(flags: list[dict]) -> list[dict]:
    return sorted(flags, key=lambda f: ORDER.get(f["status"], 3))


def validate(text: str, flags: list[dict]) -> str | None:
    """Why the LLM text is unusable, or None.

    Bullet i must match sorted flag i: its action, drug and rule_id, and every number it states must
    appear in that flag's evidence or message. A small model will otherwise copy or invent values.
    """
    bullets = [ln.strip().lstrip("-*• ").strip() for ln in text.splitlines()
               if ln.strip().startswith(("-", "*", "•"))]
    if len(bullets) != len(flags):
        return f"expected {len(flags)} bullets, got {len(bullets)}"
    for bullet, flag in zip(bullets, _sorted(flags), strict=True):
        low = bullet.lower()
        if not low.startswith(action(flag).lower()):
            return f"wrong action for {flag['rule_id']}: {bullet[:60]}"
        if flag["rule_id"] not in bullet or flag["drug"].lower() not in low:
            return f"bullet does not name {flag['drug']} / {flag['rule_id']}"
        allowed = set(NUMBER.findall(json.dumps(flag, default=str)))
        stated = set(NUMBER.findall(bullet.replace(flag["rule_id"], "")))
        if extra := stated - allowed:
            return f"numbers not in the evidence: {sorted(extra)}"
    return None


def fallback_summary(flags: list[dict]) -> str:
    lines = []
    for f in _sorted(flags):
        ev = "; ".join(e["detail"] for e in f["evidence"])
        lines.append(f"- {action(f)} {f['drug']}: {f['message']} [{f['rule_id']}; {ev}]")
    return "\n".join(lines)


def _default_model(base: str) -> str:
    with urllib.request.urlopen(f"{base}/v1/models", timeout=3) as r:
        models = [m["id"] for m in json.load(r)["data"] if "embed" not in m["id"]]
    if not models:
        raise RuntimeError("LM Studio has no chat model loaded")
    return models[0]


def summarize(flags: list[dict], base: str = LMSTUDIO_URL, model: str = LMSTUDIO_MODEL) -> dict:
    """{'text', 'source': 'llm' | 'rules', 'model', 'error'}; never raises."""
    if not flags:
        return {"text": "No rule-based flags at this time.", "source": "rules", "model": None, "error": None}
    try:
        model = model or _default_model(base)
        body = json.dumps({
            "model": model,
            "temperature": 0,
            "max_tokens": 500,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(_compact(_sorted(flags)), default=str)},
            ],
        }).encode()
        req = urllib.request.Request(
            f"{base}/v1/chat/completions", body, {"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            text = json.load(r)["choices"][0]["message"]["content"].strip()
        if problem := validate(text, flags):
            return {"text": fallback_summary(flags), "source": "rules", "model": model,
                    "error": f"LLM output rejected: {problem}"}
        return {"text": text, "source": "llm", "model": model, "error": None}
    except (urllib.error.URLError, TimeoutError, RuntimeError, KeyError, ValueError) as e:
        return {"text": fallback_summary(flags), "source": "rules", "model": None, "error": str(e)}
