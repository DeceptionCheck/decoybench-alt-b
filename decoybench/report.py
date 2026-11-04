"""Evidence export: JSONL rows, summary statistics, pinned hashes.

Completeness measures response completion (a finished HTTP exchange,
whatever the status). Status match rate is reported separately and
never conflated with completeness. Semantic and model-qualification
checks do not exist in this offering and are labeled as such.
"""

from __future__ import annotations

import datetime
import hashlib
import json


def _percentile(sorted_values: list, pct: float) -> float:
    if not sorted_values:
        return 0.0
    rank = (len(sorted_values) - 1) * (pct / 100.0)
    low = int(rank)
    high = min(low + 1, len(sorted_values) - 1)
    frac = rank - low
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * frac


def _utc_now() -> str:
    return datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _finish_counts(rows: list) -> dict:
    counts: dict = {}
    for row in rows:
        reason = row.get("finish_reason", "transport_error")
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _case_stats(rows: list) -> list:
    by_case: dict = {}
    for row in rows:
        by_case.setdefault(row["id"], []).append(row)
    cases = []
    for case_id in sorted(by_case):
        group = by_case[case_id]
        completed = [row for row in group
                     if row.get("finish_reason") == "completed"]
        latencies = sorted(row["latency_ms"] for row in completed)
        totals = sorted(row.get("total_ms", 0.0) for row in completed)
        matches = sum(1 for row in completed if row.get("match"))
        check_kinds = sorted({row.get("check_kind", "lexical-status")
                              for row in group})
        cases.append({
            "id": case_id,
            "samples": len(group),
            "completed": len(completed),
            "match_rate": round(matches / len(completed), 4) if completed else 0.0,
            "p50_ms": round(_percentile(latencies, 50), 2),
            "p95_ms": round(_percentile(latencies, 95), 2),
            "max_ms": round(max(latencies), 2) if latencies else 0.0,
            "p95_total_ms": round(_percentile(totals, 95), 2),
            "check_kinds": check_kinds,
        })
    return cases


def _build_summary(rows: list, unreplayable: list, meta: dict) -> dict:
    total = len(rows)
    completed = sum(1 for row in rows
                    if row.get("finish_reason") == "completed")
    matched = sum(1 for row in rows
                  if row.get("finish_reason") == "completed"
                  and row.get("match"))
    throttled = sum(1 for row in rows if row.get("throttled"))
    transport = sum(1 for row in rows
                    if row.get("finish_reason") == "transport_error")
    timed_out = sum(1 for row in rows
                    if row.get("finish_reason") == "timeout")
    truncated = sum(1 for row in rows
                    if row.get("finish_reason") == "truncated")
    required = meta.get("require_completeness", 1.0)
    require_match = meta.get("require_match_rate", 1.0)
    completeness = round(completed / total, 4) if total else 0.0
    match_rate = round(matched / completed, 4) if completed else 0.0
    bad_transport = (transport or timed_out) \
        and not meta.get("tolerate_transport")
    passed = (completeness >= required and match_rate >= require_match
              and not bad_transport)
    summary = {
        "tool": "decoybench-v1",
        "finished_utc": _utc_now(),
        "target": meta.get("target", "rescore"),
        "mode": meta.get("mode", "ingress"),
        "runs": meta.get("runs", 0),
        "clients": meta.get("clients", 0),
        "corpus_origin": meta.get("corpus_origin", "unknown"),
        "corpus_sha256": meta.get("corpus_sha256", ""),
        "planned_rows": meta.get("planned_rows", total),
        "rows": total,
        "finish_reasons": _finish_counts(rows),
        "completeness": completeness,
        "match_rate": match_rate,
        "truncated": truncated,
        "throttled": throttled,
        "require_completeness": required,
        "require_match_rate": require_match,
        "pass": passed,
        "semantic_checks": "not implemented (lexical status/body checks only)",
        "model_qualification": "not implemented (no model in this offering)",
        "unreplayable_rows": unreplayable,
        "cases": _case_stats(rows),
    }
    return summary


def _planned_rows(cases: list, runs: int) -> int:
    per_run = 0
    for case in cases:
        steps = case.get("flow")
        per_run += len(steps) if steps else 1
    return per_run * runs


def summarize(rows: list, target: str, corpus_origin: str,
              corpus_sha: str, runs: int, clients: int, cases: list,
              require_completeness: float = 1.0,
              require_match_rate: float = 1.0,
              tolerate_transport: bool = False) -> dict:
    return _build_summary(rows, [], {
        "target": target,
        "mode": "ingress",
        "runs": runs,
        "clients": clients,
        "corpus_origin": corpus_origin,
        "corpus_sha256": corpus_sha,
        "planned_rows": _planned_rows(cases, runs),
        "require_completeness": require_completeness,
        "require_match_rate": require_match_rate,
        "tolerate_transport": tolerate_transport,
    })


def rescore(rows: list, meta: dict) -> dict:
    """Recompute a summary offline over ALL saved rows.

    Failed and unattempted rows are retained with their original
    planned denominator; rows without a finished response are listed
    as unreplayable, never re-fetched or dropped. One success plus
    one timeout therefore stays 50% complete and FAILs.
    """
    unreplayable = [
        {"id": row.get("id"), "run": row.get("run"),
         "reason": row.get("finish_reason"), "error": row.get("error", "")}
        for row in rows if row.get("finish_reason") != "completed"
    ]
    summary = _build_summary(rows, unreplayable, dict(meta, mode="rescore"))
    summary["note"] = ("rescore is offline: rows are never re-fetched; "
                       "non-completed rows are retained and listed as "
                       "unreplayable")
    return summary


def write_jsonl(rows: list, path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def read_jsonl(path: str) -> list:
    rows = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_summary(summary: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()
