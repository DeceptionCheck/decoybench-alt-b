# Security and limitations

DecoyBench is a local measurement tool, not a hardened appliance.

- Ingress mode makes outbound HTTP requests to the operator-supplied
  `--target`. Targets must be loopback (`localhost`, loopback IPs
  such as 127.0.0.1 or the IPv6 loopback) unless `--allow-remote`
  explicitly; without that flag a non-loopback target is refused
  before any request is sent. Aim it only at decoys you operate.
- The tool binds no listening ports itself. Its test suite starts
  stub HTTP servers on 127.0.0.1 with ephemeral ports for the
  duration of the run only.
- Rescore mode (`--mode rescore`) reads local rows files and makes
  no network connections at all.
- Results (`rows.jsonl`, `summary.json`, `meta.json`, HTML report)
  are written to the operator-chosen `--out` directory. Treat that
  directory as your own working data; it is never uploaded.
- Standard library only: no third-party runtime dependencies, no
  model, account, or private endpoint required, no secrets in
  version control.
- Latency figures are client-side observations of synthetic
  fixtures, not production measurements. Semantic and model
  qualification checks are labeled NOT IMPLEMENTED wherever they
  would otherwise appear.
