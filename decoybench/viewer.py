"""Static HTML viewer: render a DecoyBench summary file, no server needed."""

from __future__ import annotations

import argparse
import html
import json


def render(summary: dict) -> str:
    table_rows = "".join(
        "<tr><td>{cid}</td><td>{n}</td><td>{done}</td><td>{m}</td>"
        "<td>{p50}</td><td>{p95}</td><td>{mx}</td><td>{tot}</td>"
        "<td>{kinds}</td></tr>".format(
            cid=html.escape(str(case["id"])),
            n=case["samples"],
            done=case["completed"],
            m=case["match_rate"],
            p50=case["p50_ms"],
            p95=case["p95_ms"],
            mx=case["max_ms"],
            tot=case.get("p95_total_ms", 0.0),
            kinds=html.escape(",".join(case.get("check_kinds", []))),
        )
        for case in summary.get("cases", [])
    )
    reasons = "".join(
        f"<li>{html.escape(str(name))}: {count}</li>"
        for name, count in sorted(summary.get("finish_reasons", {}).items())
    ) or "<li>none</li>"
    unreplayable = "".join(
        "<li>{rid} run {run}: {why}</li>".format(
            rid=html.escape(str(item.get("id", ""))),
            run=item.get("run", "?"),
            why=html.escape(str(item.get("reason", ""))),
        )
        for item in summary.get("unreplayable_rows", [])
    )
    unreplayable_block = (
        f"<div><b>Unreplayable rows.</b><ul>{unreplayable}</ul></div>"
        if unreplayable else "")
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<title>DecoyBench report</title>"
        "<style>table{{border-collapse:collapse}}"
        "th,td{{border:1px solid #999;padding:6px 10px;text-align:left}}"
        "a:focus{{outline:2px solid #0E6E6E;outline-offset:2px}}</style>"
        "</head><body>"
        "<h1>DecoyBench report</h1>"
        "<div>Target: {target} | Mode: {mode} | Runs: {rowcount_runs} | "
        "Clients: {clients} | Rows: {rowcount} (planned {planned})</div>"
        "<div>Completeness (responses completed): {comp} | "
        "Status match rate: {match} | Pass: {passed}</div>"
        "<div>Corpus: {origin} <code>{sha}</code></div>"
        "<div>Finished: {when}</div>"
        "<div><b>Finish reasons.</b><ul>{reasons}</ul></div>"
        "<div>Semantic checks: {sem} | Model qualification: {mod}</div>"
        "{unreplayable}"
        "<table><tr><th>Case</th><th>Samples</th><th>Completed</th>"
        "<th>Match rate</th><th>p50 ms</th><th>p95 ms</th><th>max ms</th>"
        "<th>total p95 ms</th><th>Check kinds</th></tr>{tablerows}</table>"
        "<div>ttfb_ms marks the first body byte; total p95 ms is the "
        "true full-response duration.</div>"
        "<div>Neutral synthetic fixtures only. Latency measured "
        "client-side per request.</div>"
        "</body></html>"
    ).format(
        target=html.escape(str(summary.get("target", ""))),
        mode=html.escape(str(summary.get("mode", ""))),
        rowcount_runs=summary.get("runs", 0),
        clients=summary.get("clients", 0),
        rowcount=summary.get("rows", 0),
        planned=summary.get("planned_rows", 0),
        comp=summary.get("completeness", 0.0),
        match=summary.get("match_rate", 0.0),
        passed=summary.get("pass", False),
        origin=html.escape(str(summary.get("corpus_origin", ""))),
        sha=html.escape(str(summary.get("corpus_sha256", ""))),
        when=html.escape(str(summary.get("finished_utc", ""))),
        reasons=reasons,
        sem=html.escape(str(summary.get("semantic_checks", ""))),
        mod=html.escape(str(summary.get("model_qualification", ""))),
        unreplayable=unreplayable_block,
        tablerows=table_rows,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Render a DecoyBench report.")
    parser.add_argument("summary", help="summary.json from the CLI run")
    parser.add_argument("--out", required=True, help="output HTML path")
    args = parser.parse_args(argv)
    with open(args.summary, "r", encoding="utf-8") as handle:
        summary = json.load(handle)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(render(summary))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
