"""Neutral fixture corpus: built-in cases plus pinned JSON loading."""

from __future__ import annotations

import hashlib
import json

BUILTIN_CORPUS = [
    {"id": "root", "method": "GET", "path": "/", "expect": 200},
    {"id": "overview", "method": "GET", "path": "/overview", "expect": 200},
    {"id": "about", "method": "GET", "path": "/about", "expect": 200},
    {"id": "visit-begin", "method": "GET", "path": "/visit", "expect": 200},
    {"id": "visit-station-nocookie", "method": "GET",
     "path": "/visit/station", "expect": 200},
    {"id": "ready-without-cookie", "method": "POST",
     "path": "/visit/ready", "body": "", "expect": 200},
    {"id": "act-without-cookie", "method": "POST",
     "path": "/visit/act", "body": "", "expect": 200},
    {"id": "unknown-path", "method": "GET",
     "path": "/no-such-path", "expect": 404},
    {"id": "bad-percent", "method": "GET", "path": "/%ZZ", "expect": 400},
    {"id": "long-path", "method": "GET",
     "path": "/" + "a" * 300, "expect": 400},
    {"id": "delete-without-cookie", "method": "DELETE",
     "path": "/visit/ready", "expect": 200},
    {"id": "empty-post", "method": "POST",
     "path": "/visit/ask", "body": "", "expect": 200},
    {"id": "visit-flow", "check_kind": "lexical-status",
     "flow": [
         {"id": "visit-flow-begin", "method": "GET", "path": "/visit",
          "expect": 200, "check_kind": "lexical-status"},
         {"id": "visit-flow-ready", "method": "POST",
          "path": "/visit/ready", "body": "", "expect": 200,
          "check_kind": "lexical-status"},
     ]},
]


def _normalize_step(entry: dict, where: str) -> dict:
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be an object")
    method = str(entry.get("method", "GET")).upper()
    if method not in ("GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS"):
        raise ValueError(f"unsupported method {method!r} in {where}")
    return {
        "id": str(entry.get("id", "")),
        "method": method,
        "path": str(entry.get("path", "/")),
        "body": str(entry.get("body", "")),
        "expect": int(entry.get("expect", 200)),
        "check_kind": str(entry.get("check_kind", "lexical-status")),
        "match_body_contains": entry.get("match_body_contains"),
    }


def corpus_hash(cases: list) -> str:
    canonical = json.dumps(cases, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_corpus(path=None) -> tuple:
    """Return (cases, sha256, origin). Built-in when path is None."""
    if path is None:
        cases = [dict(case) for case in BUILTIN_CORPUS]
        return cases, corpus_hash(cases), "builtin"
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("corpus file must hold a JSON list of cases")
    cases = []
    for position, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ValueError("each corpus case must be an object")
        if entry.get("flow"):
            steps = entry["flow"]
            if not isinstance(steps, list) or not steps:
                raise ValueError("flow cases need a non-empty steps list")
            cases.append({
                "id": str(entry.get("id", f"flow-{position}")),
                "flow": [_normalize_step(step, f"flow {position}")
                         for step in steps],
                "check_kind": str(entry.get("check_kind",
                                            "lexical-status")),
            })
        else:
            cases.append(_normalize_step(entry, f"case {position}"))
    return cases, corpus_hash(cases), str(path)
