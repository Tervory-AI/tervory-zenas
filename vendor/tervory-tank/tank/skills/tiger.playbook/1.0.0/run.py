"""Pure playbook operations from #391; persistence remains in Tiger's room."""
import copy
import importlib.util
import json
from pathlib import Path

_spec = importlib.util.spec_from_file_location("playbook_rules", Path(__file__).with_name("rules.py"))
rules = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rules)


async def run(payload: dict, ctx) -> dict:
    del ctx
    if not isinstance(payload, dict) or len(json.dumps(payload)) > 4000000:
        raise ValueError("Invalid playbook input")
    operation = payload.get("operation")
    if operation == "public_page":
        value = rules.public_page(payload["url"], payload["page"])
        value = list(value) if value else None
    elif operation == "public_marker":
        value = rules.public_marker(payload.get("layout"))
    elif operation == "read_page":
        value = rules._guard.read_page(payload["html"], payload.get("signature"), payload.get("extracted"))
    elif operation == "learn_layout":
        value = rules._guard.learn_layout(payload["html"], payload.get("extracted"))
    elif operation == "advance":
        state = copy.deepcopy(payload["state"])
        changed = rules._advance(state, payload["page_hash"], payload["generation"],
                                 payload.get("attempted"), payload.get("confirmed"))
        value = {"changed": changed, "state": state}
    else:
        raise ValueError("Unsupported playbook operation")
    return {"value": value, "model_calls": 0}
