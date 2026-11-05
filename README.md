# DecoyBench v1

> **This is DecoyBench implementation B, version v1** (repository `decoybench-alt-b`), one of three independent implementations built from the same specification and tests. The reference implementation is [decoybench](https://github.com/DeceptionCheck/decoybench); the other alternate is [decoybench-alt-a](https://github.com/DeceptionCheck/decoybench-alt-a). They have different commands and result formats; keep each in its own folder or virtual environment. Results and the model program: https://meta.deceptioncheck.ai/research/DecoyBench-Report.html

Stdlib-only harness that replays a neutral, synthetic fixture
corpus against a **localhost decoy target** (for example the
pump-station-zero v2 HMI), measures latency and completion, and exports
JSONL evidence plus a static HTML report.

- No third-party dependencies; no model calls; no private prompts.
- Refuses non-loopback targets unless `--allow-remote` is passed.
- Fixtures are pinned by SHA-256 in every summary.

## Modes

- `ingress` (default): live HTTP replay. `--runs` (1/2/4/8)
  repetitions are distinct from `--clients` (1/2/4/8) worker threads.
  Each worker owns a cookie jar; `flow` corpus cases run their steps
  on one worker so multi-step sessions keep the correct cookie.
- `rescore`: offline recompute from a saved `rows.jsonl`. Rows without
  a finished response are listed as unreplayable, never re-fetched.

## Row contract

- `finish_reason`: `completed` (any HTTP status received), `timeout`
  (absolute `--deadline-ms`, enforced against trickle responses),
  `too_large` (over `--max-bytes`), `truncated` (body shorter than the
  declared Content-Length, for any status), `transport_error`
  (connection refused/reset/DNS, distinct from HTTP).
- TTFB is measured from request start. Strict acceptance fails on
  completeness below `--require-completeness` (default 1.0) AND on
  status match rate below `--require-match-rate` (default 1.0), even
  when every response completed. Rescore keeps all rows and the
  planned denominator: one success plus one timeout stays 50% and FAILs.
- HTTP error statuses (400/403/404/429/503) are preserved as real
  responses. 429 sets `throttled: true` and stays visible.
- `completeness` = responses completed / rows. Status match rate is
  reported separately and never conflated with completeness.
- Checks are lexical (`lexical-status`, optional `match_body_contains`
  substring). Semantic and model-qualification checks are labeled
  NOT IMPLEMENTED in every summary and report.
- First-body-byte timing (`ttfb_ms`) is recorded per request;
  response headers are already consumed by urlopen, so the first
  byte read is body. `total_ms` records the true full-response
  duration (last byte). TTFB is not a substitute for
  full-response timing; both are reported, including per-case
  `p95_total_ms`.

## Exits

0 pass, 2 usage/corpus/file errors, 3 completeness below
`--require-completeness` (default 1.0) or unexpected
transport/timeout failures (unless `--tolerate-transport`).

## Install in a separate environment

Extract the release archive into an EMPTY directory and run from
there. This package is named `decoybench`, which collides with
The alternate benchmark package of the same module name: never
copy both trees into one directory, never install both into one
interpreter path, and use separate directories (or virtual
environments) per implementation.

## Use

```powershell
python -m decoybench.cli --target http://127.0.0.1:8280 --runs 4 --clients 4 --out results\r4c4
python -m decoybench.cli --mode rescore --rescore results\r4c4\rows.jsonl --summary results\r4c4\summary.json --out results\re4c4
python -m decoybench.viewer results\r4c4\summary.json --out results\r4c4\report.html
```

Intentional-failure workflow: stop the target, run ingress (transport
errors, exit 3), then rescore the rows (unreplayable listed, exit 3).
An offline sample lives in `examples/offline-rows.jsonl`.

Open the HTML report in a browser for the owner/operator visual check.
Python command outputs are authoritative.

## Known measurement limitations (do not overclaim)

- TTFB marks the first body byte, not full-response delivery.
  It does not measure complete-response latency.
- The absolute deadline bounds each request's observed lifetime, but
  abandoned reader threads and sockets clean up lazily; resource
  cleanup under sustained timeout load has limited acceptance.
- `--total-deadline-ms` sets an overall matrix budget; cases starting
  after it expires are recorded as `unattempted`, never dropped, so
  rows still equal the planned denominator. There is no preemption of
  an in-flight request: the budget gates attempt start only.
- Strict acceptance (completeness, match rate, transport) is a
  lexical/transport gate only. It is not model qualification,
  usability evidence, or production certification.

## License

MIT; see [LICENSE](LICENSE), [NOTICE.md](NOTICE.md) and [SECURITY.md](SECURITY.md).
