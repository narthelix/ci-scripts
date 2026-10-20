#!/usr/bin/env python3
"""Tests for test_results.py — run with `python3 test_test_results.py`.

The fixtures are the shapes the real runners write (pytest, vitest, Maestro JUnit; jest JSON).
The property that matters most is the last one: nothing here may turn a build red.
"""

from __future__ import annotations

import http.server
import json
import pathlib
import sys
import tempfile
import threading

import test_results as tr

PYTEST = """<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" tests="3">
<testcase classname="tests.test_a" name="test_ok" time="0.010"/>
<testcase classname="tests.test_a" name="test_bad" time="1.500"><failure message="assert 1 == 2">trace</failure></testcase>
<testcase classname="tests.test_a" name="test_skip" time="0"><skipped message="later"/></testcase>
</testsuite></testsuites>"""

MAESTRO = """<?xml version='1.0' encoding='UTF-8'?><testsuites><testsuite name="Test Suite" tests="2">
<testcase id="giris" name="giris" classname="giris" time="73.0"/>
<testcase id="kopru" name="kopru" classname="kopru" time="40.0"><error>Element not found</error></testcase>
</testsuite></testsuites>"""

JEST = {"testResults": [{"name": "/r/src/a.test.ts", "assertionResults": [
    {"ancestorTitles": ["login"], "title": "sends code", "status": "passed", "duration": 12},
    {"ancestorTitles": ["login"], "title": "rejects", "status": "failed", "duration": 30,
     "failureMessages": ["Expected 1\nReceived 2"]},
    {"ancestorTitles": [], "title": "todo", "status": "pending", "duration": None},
]}]}


def write(files: dict[str, str]) -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp())
    for rel, body in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body, encoding="utf-8")
    return d


class Sink(http.server.BaseHTTPRequestHandler):
    bodies: list[dict] = []

    def do_POST(self):  # noqa: N802
        Sink.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_):
        pass


def main() -> int:
    fails = 0

    def check(name: str, ok: bool, detail: object = ""):
        nonlocal fails
        if not ok:
            fails += 1
            print(f"FAIL  {name}: {detail}")
        else:
            print(f"ok    {name}")

    d = write({"r/pytest.xml": PYTEST, "r/maestro.xml": MAESTRO, "r/jest.json": json.dumps(JEST)})

    py = tr.read(str(d / "r/pytest.xml"))
    check("pytest: three statuses", [c.status for c in py] == ["passed", "failed", "skipped"],
          [c.status for c in py])
    check("pytest: failure message kept", py[1].message == "assert 1 == 2", py[1].message)
    check("pytest: class joins name", py[0].name == "tests.test_a › test_ok", py[0].name)

    ma = tr.read(str(d / "r/maestro.xml"))
    check("maestro: <error> is a failure, name not doubled",
          [(c.name, c.status) for c in ma] == [("giris", "passed"), ("kopru", "failed")],
          [(c.name, c.status) for c in ma])

    je = tr.read(str(d / "r/jest.json"))
    check("jest: pending is skipped, ms -> s",
          [c.status for c in je] == ["passed", "failed", "skipped"] and je[1].seconds == 0.03,
          [(c.status, c.seconds) for c in je])
    check("jest: ancestors join the title", je[1].name == "login › rejects", je[1].name)

    cases, warnings = tr.collect([str(d / "r/*.xml"), str(d / "r/*.json")])
    check("glob collects all three files", len(cases) == 8 and not warnings, (len(cases), warnings))

    md = tr.summary(cases, "Unit")
    check("summary: red mark, counts, failures listed",
          md.startswith("## ❌ Unit") and "| 8 | 3 | 3 | 2 |" in md and "`kopru` (kopru) — Element not found" in md,
          md)
    check("summary: green when nothing failed",
          tr.summary(py[:1], "Unit").startswith("## ✅"), tr.summary(py[:1], "Unit"))

    long = tr.parse_junit(f'<testsuite><testcase name="x"><failure message="{"y" * 900}"/></testcase></testsuite>')
    check("message truncated", len(long[0].message) == tr.MESSAGE_MAX, len(long[0].message))

    bodies = tr.streams(cases, {"repo": "r", "suite": "unit"}, {"sha": "abc"}, 1_000)
    stamps = [v[0] for b in bodies for s in b["streams"] for v in s["values"]]
    check("streams: one run line + cases, distinct stamps",
          len(stamps) == 9 and len(set(stamps)) == 9
          and bodies[0]["streams"][0]["stream"] == {"repo": "r", "suite": "unit", "kind": "run"},
          stamps)
    line = tr.streams(py, {}, {"sha": "abc"}, 0)[1]["streams"][0]["values"][1][1]
    check("streams: line carries context and status",
          json.loads(line) == {
              "sha": "abc", "name": "tests.test_a › test_bad", "file": "tests.test_a",
              "status": "failed", "seconds": 1.5, "message": "assert 1 == 2"},
          line)
    big = tr.streams([tr.Case("n", "f", "passed", 0)] * (tr.BATCH + 1), {}, {}, 0)
    check("streams: batched", len(big) == 3, len(big))

    srv = http.server.HTTPServer(("127.0.0.1", 0), Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/loki/api/v1/push"
    summ = d / "summary.md"
    rc = tr.main([str(d / "r/*.xml"), "--summary", str(summ), "--push-url", url,
                  "--repo", "r", "--suite", "e2e", "--event", "nightly", "--context", "platform=ios"])
    srv.shutdown()
    labels = [s["stream"] for b in Sink.bodies for s in b["streams"]]
    check("main: summary written, pushed with labels",
          rc == 0 and "## ❌ Tests" in summ.read_text()
          and labels[0] == {"repo": "r", "suite": "e2e", "event": "nightly", "kind": "run"}
          and json.loads(Sink.bodies[0]["streams"][0]["values"][0][1])["platform"] == "ios",
          (rc, labels))

    # Never red: no files, a broken file, an unreachable receiver.
    broken = write({"bad.xml": "<testsuite><testcase", "ok.xml": PYTEST})
    rc = tr.main([str(broken / "*.xml"), str(broken / "none-*.xml"),
                  "--push-url", "http://127.0.0.1:9/loki/api/v1/push"])
    check("broken file + no receiver still exit 0", rc == 0, rc)
    check("nothing matched still exit 0", tr.main([str(broken / "nothing-*.xml")]) == 0)

    print(f"\n{'FAILED' if fails else 'all passed'} ({fails} failure(s))")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
