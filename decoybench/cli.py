"""Command line: ingress replay or offline rescore of fixture evidence."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
import urllib.parse

from .corpus import load_corpus
from .report import (file_sha256, read_jsonl, rescore, summarize,
                     write_jsonl, write_summary)
from .runner import (DEFAULT_MAX_BYTES, DEFAULT_TIMEOUT_S, run_matrix)


def _is_loopback(target: str) -> bool:
    """True only for loopback targets: localhost or a loopback IP.

    "localhost" is a name, not an address literal, so it is handled
    explicitly; every other host must parse as an IP address with
    is_loopback true (covers the whole IPv4 loopback range, the
    IPv6 loopback, and other loopback ranges while refusing LAN,
    routable, and malformed hosts).
    """
    try:
        host = urllib.parse.urlparse(target).hostname or ""
    except ValueError:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _exit_for(summary: dict) -> int:
    return 0 if summary.get("pass") else 3


def _run_ingress(args) -> int:
    try:
        cases, corpus_sha, origin = load_corpus(args.corpus)
    except (OSError, ValueError) as exc:
        print(f"bad corpus: {exc}", file=sys.stderr)
        return 2

    total_deadline_s = (args.total_deadline_ms / 1000.0
                        if args.total_deadline_ms is not None else None)
    rows = run_matrix(args.target, cases, args.runs, clients=args.clients,
                      timeout_s=args.deadline_ms / 1000.0,
                      max_bytes=args.max_bytes,
                      use_sessions=not args.no_session_cookies,
                      total_deadline_s=total_deadline_s)
    os.makedirs(args.out, exist_ok=True)
    rows_path = os.path.join(args.out, "rows.jsonl")
    summary_path = os.path.join(args.out, "summary.json")
    meta_path = os.path.join(args.out, "meta.json")
    write_jsonl(rows, rows_path)
    summary = summarize(rows, args.target, origin, corpus_sha, args.runs,
                        args.clients, cases,
                        require_completeness=args.require_completeness,
                        require_match_rate=args.require_match_rate,
                        tolerate_transport=args.tolerate_transport)
    summary["rows_sha256"] = file_sha256(rows_path)
    write_summary(summary, summary_path)
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump({
            "target": args.target,
            "runs": args.runs,
            "clients": args.clients,
            "corpus_origin": origin,
            "corpus_sha256": corpus_sha,
            "require_completeness": args.require_completeness,
            "require_match_rate": args.require_match_rate,
            "tolerate_transport": args.tolerate_transport,
            "total_deadline_ms": args.total_deadline_ms,
        }, handle, indent=2, sort_keys=True)
    print(f"runs={args.runs} clients={args.clients} rows={len(rows)} "
          f"planned={summary['planned_rows']} "
          f"completeness={summary['completeness']} "
          f"match_rate={summary['match_rate']} pass={summary['pass']}")
    print(f"wrote {rows_path}")
    print(f"wrote {summary_path}")
    return _exit_for(summary)


def _run_rescore(args) -> int:
    try:
        rows = read_jsonl(args.rescore)
    except (OSError, ValueError) as exc:
        print(f"bad rows file: {exc}", file=sys.stderr)
        return 2
    meta = {}
    if args.summary:
        try:
            with open(args.summary, "r", encoding="utf-8") as handle:
                prior = json.load(handle)
            for key in ("target", "runs", "clients", "corpus_origin",
                        "corpus_sha256", "planned_rows",
                        "require_completeness", "require_match_rate",
                        "tolerate_transport"):
                if key in prior:
                    meta[key] = prior[key]
        except (OSError, ValueError) as exc:
            print(f"bad summary file: {exc}", file=sys.stderr)
            return 2
    meta.setdefault("require_completeness", args.require_completeness)
    meta.setdefault("require_match_rate", args.require_match_rate)
    meta.setdefault("tolerate_transport", args.tolerate_transport)
    summary = rescore(rows, meta)
    os.makedirs(args.out, exist_ok=True)
    summary_path = os.path.join(args.out, "summary.json")
    write_summary(summary, summary_path)
    print(f"rows={len(rows)} unreplayable={len(summary['unreplayable_rows'])} "
          f"completeness={summary['completeness']} pass={summary['pass']}")
    print(f"wrote {summary_path}")
    return _exit_for(summary)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay neutral fixtures against a localhost decoy, "
                    "or rescore saved rows offline.")
    parser.add_argument("--mode", choices=("ingress", "rescore"),
                        default="ingress")
    parser.add_argument("--target",
                        help="base URL, e.g. http://127.0.0.1:8180")
    parser.add_argument("--runs", type=int, default=4, choices=(1, 2, 4, 8))
    parser.add_argument("--clients", type=int, default=1,
                        choices=(1, 2, 4, 8))
    parser.add_argument("--corpus", default=None,
                        help="optional JSON fixture file (default: built-in)")
    parser.add_argument("--deadline-ms", type=float, default=DEFAULT_TIMEOUT_S * 1000.0)
    parser.add_argument("--total-deadline-ms", type=float, default=None,
                        help="overall matrix budget; cases starting after it "
                             "expires are recorded as unattempted, never dropped")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--no-session-cookies", action="store_true",
                        help="disable per-worker cookie jars")
    parser.add_argument("--require-completeness", type=float, default=1.0)
    parser.add_argument("--require-match-rate", type=float, default=1.0,
                        help="strict acceptance also fails when the status "
                             "match rate falls below this, even if every "
                             "response completed")
    parser.add_argument("--tolerate-transport", action="store_true",
                        help="pass despite transport failures")
    parser.add_argument("--allow-remote", action="store_true",
                        help="permit non-loopback targets (operator override)")
    parser.add_argument("--rescore",
                        help="rows.jsonl to rescore offline (rescore mode)")
    parser.add_argument("--summary",
                        help="prior summary.json for rescore metadata")
    parser.add_argument("--out", required=True, help="output directory")
    args = parser.parse_args(argv)

    if args.mode == "rescore":
        if not args.rescore:
            print("--rescore requires a rows.jsonl file", file=sys.stderr)
            return 2
        return _run_rescore(args)
    if not args.target:
        print("ingress mode requires --target", file=sys.stderr)
        return 2
    if not _is_loopback(args.target) and not args.allow_remote:
        print("refusing non-loopback target without --allow-remote",
              file=sys.stderr)
        return 2
    return _run_ingress(args)


if __name__ == "__main__":
    raise SystemExit(main())
