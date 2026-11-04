"""Replay engine: run fixture cases against a target, record timings.

Threading model: `clients` worker threads share the case queue; each
worker owns a cookie jar, so multi-step flows keep the correct
worker's session. `runs` repeats the whole corpus per matrix and is
independent of client count. Every row is tagged with its run index
and worker identity.
"""

from __future__ import annotations

import http.cookiejar
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_BYTES = 1024 * 1024

_local = threading.local()


def _opener(use_sessions: bool):
    if not use_sessions:
        return urllib.request.build_opener()
    jar = getattr(_local, "jar", None)
    if jar is None:
        jar = http.cookiejar.CookieJar()
        _local.jar = jar
        _local.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar))
    return _local.opener


def _jar_cookies(target: str) -> int:
    jar = getattr(_local, "jar", None)
    if jar is None:
        return 0
    try:
        host = urllib.parse.urlparse(target).hostname or ""
    except ValueError:
        return 0
    count = 0
    for cookie in jar:
        domain = (cookie.domain or "").lstrip(".").lower()
        if domain and host.lower().endswith(domain):
            count += 1
    return count


def _read_with_deadline(response, max_bytes: int,
                        remaining_s: float) -> tuple:
    """Read up to max_bytes+1 in a daemon thread joinable by deadline.

    Returns (payload, first_byte_at). Raises TimeoutError when the
    absolute deadline passes first, so trickle responses cannot run
    past it one slow recv at a time.
    """
    box = {}

    def _go():
        # Read ONE byte first so ttfb_ms marks the first body byte
        # (response headers are already consumed by urlopen), not the
        # return of a buffered whole-body read. last_at marks the
        # final byte, so total_ms is the true response duration.
        try:
            first = response.read(1)
            box["first_at"] = time.monotonic()
            chunks = [first] if first else []
            total = len(first)
            while total <= max_bytes:
                piece = response.read(min(65536, max_bytes + 1 - total))
                if not piece:
                    break
                chunks.append(piece)
                total += len(piece)
            box["last_at"] = time.monotonic()
            box["data"] = b"".join(chunks)
        except Exception as exc:  # noqa: BLE001 - surfaced by caller
            box["error"] = exc

    worker = threading.Thread(target=_go, daemon=True)
    worker.start()
    worker.join(max(remaining_s, 0.0))
    if worker.is_alive():
        raise TimeoutError("absolute deadline exceeded")
    if "error" in box:
        raise box["error"]
    data = box.get("data", b"")
    return data, box.get("first_at"), box.get("last_at")


def _declared_length(headers) -> object:
    try:
        get = getattr(headers, "get", None)
        value = get("Content-Length") if get else None
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _one(target: str, case: dict, timeout_s: float, max_bytes: int,
         use_sessions: bool, worker: int, run: int,
         flow: str = "", step: int = 0) -> dict:
    url = target.rstrip("/") + case["path"]
    data = case.get("body", "").encode("utf-8") if case["method"] not in (
        "GET", "HEAD", "DELETE", "OPTIONS") else None
    request = urllib.request.Request(url, data=data, method=case["method"])
    started = time.monotonic()
    row = {
        "id": case["id"],
        "method": case["method"],
        "path": case["path"],
        "expect": case["expect"],
        "check_kind": case.get("check_kind", "lexical-status"),
        "run": run,
        "worker": worker,
        "flow": flow,
        "step": step,
        "status": 0,
        "match": False,
        "finish_reason": "transport_error",
        "throttled": False,
        "truncated": False,
        "ttfb_ms": 0.0,
        "total_ms": 0.0,
        "latency_ms": 0.0,
        "bytes": 0,
        "session_bound": False,
        "error": "",
    }
    try:
        opener = _opener(use_sessions)
        deadline_abs = started + timeout_s
        try:
            response = opener.open(request, timeout=timeout_s)
            status = response.status
            remaining = deadline_abs - time.monotonic()
            payload, first_at, last_at = _read_with_deadline(
                response, max_bytes, remaining)
            try:
                response.close()
            except (OSError, ValueError):
                pass
            truncated_by_cap = len(payload) > max_bytes
            payload = payload[:max_bytes]
            declared = _declared_length(response.headers)
            short_body = (declared is not None and case["method"] != "HEAD"
                          and not truncated_by_cap and len(payload) < declared)
        except urllib.error.HTTPError as http_err:
            status = http_err.code
            remaining = deadline_abs - time.monotonic()
            try:
                payload, first_at, last_at = _read_with_deadline(
                    http_err, max_bytes, remaining)
            except (socket.timeout, TimeoutError):
                raise
            except (OSError, ValueError):
                payload, first_at, last_at = b"", None, None
            truncated_by_cap = len(payload) > max_bytes
            payload = payload[:max_bytes]
            declared = _declared_length(http_err.headers)
            short_body = (declared is not None and not truncated_by_cap
                          and len(payload) < declared)
        row["status"] = status
        row["finish_reason"] = "completed"
        row["throttled"] = (status == 429)
        row["truncated"] = truncated_by_cap or short_body
        if short_body:
            row["finish_reason"] = "truncated"
            row["error"] = (f"declared {declared} bytes, "
                            f"received {len(payload)}")
        elif truncated_by_cap:
            row["finish_reason"] = "too_large"
        row["ttfb_ms"] = round(max(first_at - started, 0.0) * 1000.0, 2) \
            if first_at else 0.0
        row["total_ms"] = round(max(last_at - started, 0.0) * 1000.0, 2) \
            if last_at else 0.0
        row["latency_ms"] = round((time.monotonic() - started) * 1000.0, 2)
        row["bytes"] = len(payload)
        want = case.get("match_body_contains")
        body_ok = True
        if want:
            try:
                body_ok = want in payload.decode("utf-8", "replace")
            except (ValueError, TypeError):
                body_ok = False
        row["match"] = (row["finish_reason"] == "completed"
                        and status == case["expect"]) and body_ok
    except (socket.timeout, TimeoutError):
        row["latency_ms"] = round((time.monotonic() - started) * 1000.0, 2)
        row["finish_reason"] = "timeout"
        row["error"] = "timeout"
    except Exception as exc:  # noqa: BLE001 - recorded, not raised
        row["latency_ms"] = round((time.monotonic() - started) * 1000.0, 2)
        row["finish_reason"] = "transport_error"
        row["error"] = f"{type(exc).__name__}"[:80]
    if use_sessions and _jar_cookies(target):
        row["session_bound"] = True
    return row


def _unattempted(case: dict, run: int, flow: str = "",
                 step: int = 0) -> dict:
    return {
        "id": case.get("id", ""),
        "method": case.get("method", ""),
        "path": case.get("path", ""),
        "expect": case.get("expect", 0),
        "check_kind": case.get("check_kind", "lexical-status"),
        "run": run,
        "worker": threading.get_ident(),
        "flow": flow,
        "step": step,
        "status": 0,
        "match": False,
        "finish_reason": "unattempted",
        "throttled": False,
        "truncated": False,
        "ttfb_ms": 0.0,
        "total_ms": 0.0,
        "latency_ms": 0.0,
        "bytes": 0,
        "session_bound": False,
        "error": "total deadline exceeded before attempt",
    }


def _run_case(target: str, case: dict, timeout_s: float, max_bytes: int,
              use_sessions: bool, run: int, budget_abs=None) -> list:
    worker = threading.get_ident()
    steps = case.get("flow")
    if budget_abs is not None and time.monotonic() >= budget_abs:
        if steps:
            return [_unattempted(step, run, flow=case["id"], step=index)
                    for index, step in enumerate(steps)]
        return [_unattempted(case, run)]
    if steps:
        rows = []
        for index, step in enumerate(steps):
            rows.append(_one(target, step, timeout_s, max_bytes,
                             use_sessions, worker, run,
                             flow=case["id"], step=index))
        return rows
    return [_one(target, case, timeout_s, max_bytes, use_sessions,
                 worker, run)]


def run_matrix(target: str, cases: list, runs: int, clients: int = 1,
               timeout_s: float = DEFAULT_TIMEOUT_S,
               max_bytes: int = DEFAULT_MAX_BYTES,
               use_sessions: bool = True,
               total_deadline_s=None) -> list:
    """Replay the corpus `runs` times over `clients` worker threads.

    When total_deadline_s is set, cases starting after the budget
    expires are recorded as unattempted, never dropped, so rows still
    equal the planned denominator.
    """
    budget_abs = (time.monotonic() + total_deadline_s
                  if total_deadline_s is not None else None)
    rows = []
    if clients <= 1:
        for index in range(runs):
            for case in cases:
                rows.extend(_run_case(target, case, timeout_s, max_bytes,
                                      use_sessions, index, budget_abs))
        return rows
    with ThreadPoolExecutor(max_workers=clients) as pool:
        futures = [pool.submit(_run_case, target, case, timeout_s,
                               max_bytes, use_sessions, index, budget_abs)
                   for index in range(runs) for case in cases]
        for future in futures:
            rows.extend(future.result())
    rows.sort(key=lambda row: (row["run"], row["id"], row["step"]))
    return rows
