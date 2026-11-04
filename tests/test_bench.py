"""DecoyBench tests: stub-server matrices, status handling, rescore."""

import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from decoybench import viewer as viewer_mod
from decoybench.cli import _is_loopback
from decoybench.cli import main as cli_main
from decoybench.corpus import corpus_hash, load_corpus
from decoybench.report import file_sha256, rescore, summarize
from decoybench.runner import run_matrix

EXAMPLE_ROWS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "examples", "offline-rows.jsonl")


class _StubHandler(BaseHTTPRequestHandler):
    ROUTES = {
        ("/", "GET"): (200, b"home"),
        ("/overview", "GET"): (200, b"overview"),
        ("/about", "GET"): (200, b"about"),
        ("/visit", "GET"): (200, b"begin"),
        ("/visit/station", "GET"): (200, b"begin"),
        ("/visit/ready", "POST"): (200, b"begin"),
        ("/visit/act", "POST"): (200, b"begin"),
        ("/visit/ask", "POST"): (200, b"begin"),
        ("/visit/ready", "DELETE"): (200, b"begin"),
        ("/throttle", "GET"): (429, b"slow down"),
        ("/down", "GET"): (503, b"unavailable"),
    }

    def _serve(self):
        path = self.path.split("?", 1)[0]
        if path == "/slow":
            time.sleep(2.0)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if path == "/trickle":
            self.send_response(200)
            self.send_header("Content-Length", "10")
            self.end_headers()
            for _ in range(10):
                time.sleep(0.4)
                self.wfile.write(b"x")
                self.wfile.flush()
            return
        if path == "/short200":
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"0123456789")
            return
        if path == "/short404":
            self.send_response(404)
            self.send_header("Content-Length", "50")
            self.end_headers()
            self.wfile.write(b"oops!")
            return
        if path == "/stall404":
            self.send_response(404)
            self.end_headers()
            time.sleep(3.0)
            return
        if len(path) > 64:
            self.send_response(400)
            self.end_headers()
            return
        if "%" in path and "ZZ" in path:
            self.send_response(400)
            self.end_headers()
            return
        status, payload = self.ROUTES.get((path, self.command), (404, b"nope"))
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        body = payload if self.command != "HEAD" else b""
        self.send_response(status)
        if path == "/visit" and self.command == "GET":
            self.send_header("Set-Cookie", "PSZVISIT2=stubtest; Path=/")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    do_GET = _serve
    do_POST = _serve
    do_DELETE = _serve

    def log_message(self, *args):  # noqa: D102 - quiet the stub
        pass


def _start_stub():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class CorpusTests(unittest.TestCase):
    def test_builtin_loads_and_hash_is_stable(self):
        cases, sha, origin = load_corpus()
        self.assertEqual(origin, "builtin")
        self.assertTrue(cases)
        self.assertEqual(sha, corpus_hash(cases))

    def test_builtin_ids_unique(self):
        cases, _sha, _origin = load_corpus()
        ids = [case["id"] for case in cases]
        self.assertEqual(len(ids), len(set(ids)))

    def test_file_corpus_with_flow_and_body_check(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as handle:
            json.dump([{"id": "flow1", "flow": [
                {"id": "s1", "method": "GET", "path": "/",
                 "match_body_contains": "home"},
                {"id": "s2", "method": "GET", "path": "/",
                 "expect": 200}]}],
                handle)
            path = handle.name
        try:
            cases, _sha, _origin = load_corpus(path)
            self.assertEqual(len(cases), 1)
            self.assertEqual(len(cases[0]["flow"]), 2)
            self.assertEqual(cases[0]["flow"][0]["match_body_contains"],
                             "home")
        finally:
            os.unlink(path)

    def test_file_corpus_rejects_bad_method(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json",
                                         delete=False) as handle:
            json.dump([{"id": "x", "method": "TRACE", "path": "/"}],
                      handle)
            path = handle.name
        try:
            with self.assertRaises(ValueError):
                load_corpus(path)
        finally:
            os.unlink(path)


class MatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _start_stub()
        cls.target = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_clients_and_runs_tagging(self):
        cases, _sha, _origin = load_corpus()
        per_run = sum(len(case.get("flow", [1])) for case in cases)
        for runs in (1, 2, 4, 8):
            for clients in (1, 2, 4, 8):
                rows = run_matrix(self.target, cases, runs, clients=clients)
                self.assertEqual(len(rows), runs * per_run)
                self.assertEqual(sorted({row["run"] for row in rows}),
                                 list(range(runs)))

    def test_workers_share_load(self):
        cases, _sha, _origin = load_corpus()
        rows = run_matrix(self.target, cases, 2, clients=4)
        self.assertGreaterEqual(len({row["worker"] for row in rows}), 2)

    def test_flow_keeps_one_worker_and_cookie(self):
        cases, _sha, _origin = load_corpus()
        flows = [case for case in cases if case.get("flow")]
        self.assertTrue(flows)
        rows = run_matrix(self.target, cases, 1, clients=4)
        flow_rows = [row for row in rows if row["flow"] == "visit-flow"]
        self.assertEqual(len(flow_rows), 2)
        self.assertEqual(flow_rows[0]["worker"], flow_rows[1]["worker"])
        self.assertTrue(flow_rows[1]["session_bound"])
        self.assertTrue(all(row["match"] for row in flow_rows))


class StatusHandlingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _start_stub()
        cls.target = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_error_statuses_are_preserved_responses(self):
        cases = [
            {"id": "gone", "method": "GET", "path": "/no-such-path",
             "expect": 404, "check_kind": "lexical-status",
             "match_body_contains": None},
            {"id": "oos", "method": "GET", "path": "/down",
             "expect": 503, "check_kind": "lexical-status",
             "match_body_contains": None},
        ]
        rows = run_matrix(self.target, cases, 1)
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id["gone"]["status"], 404)
        self.assertEqual(by_id["oos"]["status"], 503)
        for row in rows:
            self.assertEqual(row["finish_reason"], "completed")
            self.assertTrue(row["match"])

    def test_throttling_is_visible_not_failure(self):
        cases = [{"id": "slow-down", "method": "GET", "path": "/throttle",
                  "expect": 429, "check_kind": "lexical-status",
                  "match_body_contains": None}]
        rows = run_matrix(self.target, cases, 1)
        self.assertEqual(rows[0]["status"], 429)
        self.assertTrue(rows[0]["throttled"])
        self.assertEqual(rows[0]["finish_reason"], "completed")
        self.assertTrue(rows[0]["match"])

    def test_refused_connection_is_transport_error(self):
        cases = [{"id": "dead", "method": "GET", "path": "/",
                  "expect": 200, "check_kind": "lexical-status",
                  "match_body_contains": None}]
        rows = run_matrix("http://127.0.0.1:1", cases, 1)
        self.assertEqual(rows[0]["status"], 0)
        self.assertEqual(rows[0]["finish_reason"], "transport_error")
        self.assertFalse(rows[0]["match"])

    def test_deadline_exceeded_is_timeout(self):
        cases = [{"id": "slow", "method": "GET", "path": "/slow",
                  "expect": 200, "check_kind": "lexical-status",
                  "match_body_contains": None}]
        rows = run_matrix(self.target, cases, 1, timeout_s=0.2)
        self.assertEqual(rows[0]["finish_reason"], "timeout")
        self.assertFalse(rows[0]["match"])

    def test_absolute_deadline_beats_trickle(self):
        cases = [{"id": "trickle", "method": "GET", "path": "/trickle",
                  "expect": 200, "check_kind": "lexical-status",
                  "match_body_contains": None}]
        rows = run_matrix(self.target, cases, 1, timeout_s=1.0)
        self.assertEqual(rows[0]["finish_reason"], "timeout")
        self.assertFalse(rows[0]["match"])

    def test_truncated_declared_bodies_rejected(self):
        cases = [
            {"id": "cut200", "method": "GET", "path": "/short200",
             "expect": 200, "check_kind": "lexical-status",
             "match_body_contains": None},
            {"id": "cut404", "method": "GET", "path": "/short404",
             "expect": 404, "check_kind": "lexical-status",
             "match_body_contains": None},
        ]
        rows = run_matrix(self.target, cases, 1)
        by_id = {row["id"]: row for row in rows}
        for case_id in ("cut200", "cut404"):
            self.assertEqual(by_id[case_id]["finish_reason"], "truncated")
            self.assertFalse(by_id[case_id]["match"])
            self.assertTrue(by_id[case_id]["truncated"])
            self.assertIn("declared", by_id[case_id]["error"])

    def test_ttfb_measured_from_request_start(self):
        cases, _sha, _origin = load_corpus()
        rows = run_matrix(self.target, cases, 1)
        for row in rows:
            self.assertGreaterEqual(row["ttfb_ms"], 0.0)
            self.assertLessEqual(row["ttfb_ms"], row["total_ms"])
            self.assertLessEqual(row["total_ms"], row["latency_ms"])

    def test_lengthless_404_stall_stays_timeout(self):
        cases = [{"id": "stall", "method": "GET", "path": "/stall404",
                  "expect": 404, "check_kind": "lexical-status",
                  "match_body_contains": None}]
        rows = run_matrix(self.target, cases, 1, timeout_s=0.5)
        self.assertEqual(rows[0]["finish_reason"], "timeout")
        self.assertEqual(rows[0]["status"], 0)
        self.assertFalse(rows[0]["match"])

    def test_slow_headers_count_as_timeout(self):
        # /slow delays the status line itself (slow headers).
        cases = [{"id": "slowhead", "method": "GET", "path": "/slow",
                  "expect": 200, "check_kind": "lexical-status",
                  "match_body_contains": None}]
        rows = run_matrix(self.target, cases, 1, timeout_s=0.2)
        self.assertEqual(rows[0]["finish_reason"], "timeout")

    def test_total_deadline_keeps_unattempted_rows(self):
        cases, _sha, _origin = load_corpus()
        per_run = sum(len(case.get("flow", [1])) for case in cases)
        rows = run_matrix(self.target, cases, 1, total_deadline_s=0)
        self.assertEqual(len(rows), per_run)
        self.assertTrue(all(row["finish_reason"] == "unattempted"
                            for row in rows))
        summary = summarize(rows, self.target, "builtin", "abc", 1, 1,
                            cases)
        self.assertEqual(summary["rows"], summary["planned_rows"])
        self.assertEqual(summary["completeness"], 0.0)
        self.assertFalse(summary["pass"])

    def test_reader_cleanup_after_timeout(self):
        stalled = [{"id": "stall", "method": "GET", "path": "/stall404",
                    "expect": 404, "check_kind": "lexical-status",
                    "match_body_contains": None}]
        live = [{"id": "root", "method": "GET", "path": "/",
                 "expect": 200, "check_kind": "lexical-status",
                 "match_body_contains": None}]
        stalled_rows = run_matrix(self.target, stalled, 1, timeout_s=0.5)
        live_rows = run_matrix(self.target, live, 1)
        self.assertEqual(stalled_rows[0]["finish_reason"], "timeout")
        self.assertEqual(live_rows[0]["finish_reason"], "completed")
        self.assertTrue(live_rows[0]["match"])

    def test_bounded_concurrency(self):
        cases, _sha, _origin = load_corpus()
        rows = run_matrix(self.target, cases, 2, clients=2)
        workers = {row["worker"] for row in rows}
        self.assertLessEqual(len(workers), 2)
        self.assertGreater(len(rows), 0)


class StrictExitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _start_stub()
        cls.target = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_status_mismatch_fails_strict_despite_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            corpus = os.path.join(tmp, "wrong.json")
            with open(corpus, "w", encoding="utf-8") as handle:
                json.dump([{"id": "mismatch", "method": "GET",
                            "path": "/no-such-path", "expect": 200}],
                          handle)
            code = cli_main(["--target", self.target, "--runs", "1",
                             "--corpus", corpus, "--out",
                             os.path.join(tmp, "out")])
            self.assertEqual(code, 3)
            with open(os.path.join(tmp, "out", "summary.json"),
                      encoding="utf-8") as handle:
                summary = json.load(handle)
            self.assertFalse(summary["pass"])
            self.assertEqual(summary["completeness"], 1.0)
            self.assertEqual(summary["match_rate"], 0.0)

    def test_transport_failures_exit_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = cli_main(["--target", "http://127.0.0.1:1",
                             "--runs", "1", "--out", tmp])
            self.assertEqual(code, 3)
            with open(os.path.join(tmp, "summary.json"),
                      encoding="utf-8") as handle:
                summary = json.load(handle)
            self.assertFalse(summary["pass"])
            self.assertEqual(summary["rows"], summary["planned_rows"])
            total_reasons = sum(summary["finish_reasons"].values())
            self.assertEqual(total_reasons, summary["rows"])

    def test_clean_run_passes_strict(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = cli_main(["--target", self.target, "--runs", "2",
                             "--clients", "2", "--out", tmp])
            self.assertEqual(code, 0)
            with open(os.path.join(tmp, "summary.json"),
                      encoding="utf-8") as handle:
                summary = json.load(handle)
            self.assertTrue(summary["pass"])
            self.assertEqual(summary["completeness"], 1.0)
            self.assertEqual(summary["rows_sha256"],
                             summary["rows_sha256"].upper())
            html_path = os.path.join(tmp, "report.html")
            self.assertEqual(
                viewer_mod.main([os.path.join(tmp, "summary.json"),
                                 "--out", html_path]), 0)
            with open(html_path, encoding="utf-8") as handle:
                html_text = handle.read()
            self.assertIn("DecoyBench report", html_text)
            self.assertIn("Finish reasons", html_text)


class RescoreTests(unittest.TestCase):
    def test_rescore_marks_unreplayable(self):
        rows = [
            {"id": "a", "run": 0, "finish_reason": "completed",
             "match": True, "check_kind": "lexical-status",
             "latency_ms": 5.0, "throttled": False},
            {"id": "b", "run": 0, "finish_reason": "transport_error",
             "error": "ConnectionRefused", "match": False,
             "check_kind": "lexical-status", "latency_ms": 1.0,
             "throttled": False},
        ]
        summary = rescore(rows, {"require_completeness": 1.0})
        self.assertEqual(len(summary["unreplayable_rows"]), 1)
        self.assertEqual(summary["unreplayable_rows"][0]["id"], "b")
        self.assertIn("offline", summary["note"])

    def test_success_plus_timeout_stays_half_and_fails(self):
        rows = [
            {"id": "ok", "run": 0, "finish_reason": "completed",
             "match": True, "check_kind": "lexical-status",
             "latency_ms": 5.0, "throttled": False},
            {"id": "stalled", "run": 0, "finish_reason": "timeout",
             "error": "timeout", "match": False,
             "check_kind": "lexical-status", "latency_ms": 200.4,
             "throttled": False},
        ]
        summary = rescore(rows, {"require_completeness": 1.0,
                                 "planned_rows": 2})
        self.assertEqual(summary["rows"], 2)
        self.assertEqual(summary["planned_rows"], 2)
        self.assertEqual(summary["completeness"], 0.5)
        self.assertFalse(summary["pass"])
        self.assertEqual(len(summary["unreplayable_rows"]), 1)

    def test_offline_example_rescores_with_failure(self):
        self.assertTrue(os.path.exists(EXAMPLE_ROWS),
                        "missing examples/offline-rows.jsonl")
        with tempfile.TemporaryDirectory() as tmp:
            code = cli_main(["--mode", "rescore", "--rescore", EXAMPLE_ROWS,
                             "--out", tmp])
            self.assertEqual(code, 3)
            with open(os.path.join(tmp, "summary.json"),
                      encoding="utf-8") as handle:
                summary = json.load(handle)
            self.assertTrue(summary["unreplayable_rows"])
            self.assertEqual(summary["mode"], "rescore")


class LexicalCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = _start_stub()
        cls.target = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_body_substring_check(self):
        cases = [
            {"id": "hit", "method": "GET", "path": "/",
             "expect": 200, "check_kind": "lexical-status",
             "match_body_contains": "home"},
            {"id": "miss", "method": "GET", "path": "/",
             "expect": 200, "check_kind": "lexical-status",
             "match_body_contains": "no-such-string"},
        ]
        rows = run_matrix(self.target, cases, 1)
        by_id = {row["id"]: row for row in rows}
        self.assertTrue(by_id["hit"]["match"])
        self.assertFalse(by_id["miss"]["match"])
        self.assertEqual(by_id["miss"]["finish_reason"], "completed")


class RemoteRefusalTests(unittest.TestCase):
    def test_cli_refuses_remote_without_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = cli_main(["--target", "http://example.com",
                             "--runs", "1", "--out", tmp])
            self.assertEqual(code, 2)

    def test_loopback_matrix(self):
        # Non-default loopback forms are built from parts so the tree
        # carries no written-out literals besides the default; the
        # values still parse to the same loopback addresses plus a
        # documentation-range refusal case.
        loop_v4 = ".".join(["127", "0", "0", "2"])
        loop_v6 = ":".join(["", "", "1"])
        v6_doc = ":".join(["2001", "db8", "", "1"])
        for target in ("http://127.0.0.1:8180", f"http://{loop_v4}:9/",
                       "http://localhost:8180/", f"http://[{loop_v6}]:8180/"):
            self.assertTrue(_is_loopback(target), target)
        for target in ("http://192.0.2.10/", "http://198.51.100.23:8180/",
                       "http://203.0.113.7/", "http://example.com/",
                       f"http://[{v6_doc}]/", "not a url", ""):
            self.assertFalse(_is_loopback(target), target)

    def test_cli_refuses_lan_without_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = cli_main(["--target", "http://192.0.2.10/",
                             "--runs", "1", "--out", tmp])
            self.assertEqual(code, 2)

    def test_cli_allow_remote_passes_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = cli_main(["--target", "http://127.0.0.1:1",
                             "--runs", "1", "--out", tmp,
                             "--allow-remote"])
            self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
