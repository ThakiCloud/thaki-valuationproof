"""Hash-chain of minimal audit events; external anchoring/signature is NOT implemented."""
from __future__ import annotations
import hashlib
import json


def canonical_json(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def append_event(events: list[dict], kind: str, payload: dict) -> dict:
    previous = events[-1]["event_hash"] if events else "0" * 64
    event = {"index":len(events), "previous":previous, "kind":kind, "payload":payload}
    event["event_hash"] = hashlib.sha256(canonical_json(event)).hexdigest()
    events.append(event)
    return event


def verify_events(events: list[dict]) -> bool:
    previous = "0" * 64
    for i, event in enumerate(events):
        if (event.get("index") != i or event.get("previous") != previous or
            hashlib.sha256(canonical_json({k:v for k,v in event.items() if k != "event_hash"})).hexdigest()
            != event.get("event_hash")):
            return False
        previous = event["event_hash"]
    return True
