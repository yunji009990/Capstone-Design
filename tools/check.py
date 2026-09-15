"""영역별 모의 검사를 실행하고 JSON·로그를 남긴다. 기본 영역은 dialogue다."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
SUITES = {
    "harness": ("tools/tests", "test_*.py"),
    "server": ("Server/tests", "test_*.py"),
    "registration": ("Server/tests", "test_registration.py"),
    "persona_contract": ("Server/tests", "test_persona_boundary.py"),
    "worker": ("Survey/tests", "test_*.py"),
    "web": ("Web/tests", "test_*.py"),
}
AREAS = {
    "dialogue": ("harness", "server"),
    "platform": ("harness", "registration", "persona_contract", "worker", "web"),
    "harness": ("harness",),
    "all": ("harness", "server", "worker", "web"),
}


def interpreter(value=None, root=ROOT):
    if value:
        candidate = Path(value).expanduser()
        local = candidate if candidate.is_absolute() else root / candidate
        found = str(local.resolve()) if local.is_file() else shutil.which(value)
        if not found:
            raise ValueError("Python executable not found: " + value)
        return found
    for relative in (".venv-dialogue/Scripts/python.exe", ".venv-dialogue/bin/python"):
        candidate = root / relative
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def plan(area, python, root=ROOT):
    result = []
    for name in AREAS[area]:
        directory, pattern = SUITES[name]
        result.append({"name": name, "directory": directory, "pattern": pattern,
                       "command": [python, "-B", "-m", "unittest", "discover",
                                   "-s", str(root / directory), "-p", pattern, "-q"]})
    return result


def git_state(root):
    def read(*args):
        try:
            result = subprocess.run(["git", "-C", str(root), *args],
                                    capture_output=True, timeout=5, check=True)
            return result.stdout
        except (OSError, subprocess.SubprocessError):
            return None
    head = read("rev-parse", "HEAD")
    status = read("status", "--porcelain=v1", "-z", "-uall")
    return {"head": head.decode().strip() if head else None,
            "dirty": bool(status) if status is not None else None,
            "status_sha256": hashlib.sha256(status).hexdigest() if status is not None else None}


def as_text(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")


def run_suite(suite, root, output, timeout):
    files = sorted((root / suite["directory"]).glob(suite["pattern"]))
    row = dict(suite, passed=False, tests_run=0, skipped=0, exit_code=None,
               test_sha256={p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in files if p.is_file()})
    if suite["name"] == "web":
        row["test_sha256"].update({p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in (root / suite["directory"]).glob("test_*.cjs")})
    started = time.monotonic()
    row["log"] = suite["name"] + ".log"
    if not files:
        log = "No test files matched; refusing to report success.\n"
        row["error"] = "no_test_files"
    else:
        env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        try:
            completed = subprocess.run(suite["command"], cwd=root, env=env,
                                       capture_output=True, text=True, encoding="utf-8",
                                       errors="replace", timeout=timeout)
            log = completed.stdout + completed.stderr
            count = re.search(r"^Ran (\d+) tests? in ", log, re.MULTILINE)
            skipped = re.search(r"\bskipped=(\d+)", log)
            row.update(exit_code=completed.returncode, tests_run=int(count[1]) if count else 0,
                       skipped=int(skipped[1]) if skipped else 0)
            row["passed"] = completed.returncode == 0 and row["tests_run"] > row["skipped"]
            if not row["passed"]:
                row["error"] = "tests_failed" if completed.returncode else "no_executed_tests"
        except subprocess.TimeoutExpired as error:
            log = as_text(error.stdout) + as_text(error.stderr) + "\nTest process timed out.\n"
            row["error"] = "timeout"
        except OSError as error:
            log = str(error) + "\n"
            row["error"] = "launch_failed"
    row["seconds"] = round(time.monotonic() - started, 3)
    (output / row["log"]).write_text(log, encoding="utf-8")
    return row


def save_report(output, report):
    pending = output / "report.json.tmp"
    pending.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pending.replace(output / "report.json")


def execute(area, python, output, timeout=180, root=ROOT):
    suites = plan(area, python, root)
    # 기존 검사 결과나 사용자 폴더를 덮어쓰지 않는다.
    output.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1, "kind": "offline_tests", "area": area,
              "started_at": datetime.now(timezone.utc).isoformat(), "python": python,
              "git": git_state(root), "planned_suites": [s["name"] for s in suites],
              "status": "running", "passed": False, "suites": []}
    save_report(output, report)
    interrupted = False
    try:
        for suite in suites:
            print("[check] " + suite["name"], flush=True)
            row = run_suite(suite, root, output, timeout)
            report["suites"].append(row)
            save_report(output, report)
            print("  %s: %s tests, %s skipped" %
                  ("PASS" if row["passed"] else "FAIL", row["tests_run"], row["skipped"]), flush=True)
            if not row["passed"]:
                print((output / row["log"]).read_text(encoding="utf-8"), flush=True)
    except KeyboardInterrupt:
        interrupted = True
    report["passed"] = (not interrupted and len(report["suites"]) == len(suites)
                        and all(row["passed"] for row in report["suites"]))
    report["status"] = "interrupted" if interrupted else "passed" if report["passed"] else "failed"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    report["tests_run"] = sum(row["tests_run"] for row in report["suites"])
    save_report(output, report)
    print("Report: " + str(output / "report.json"), flush=True)
    return 130 if interrupted else 0 if report["passed"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--area", choices=AREAS, default="dialogue")
    parser.add_argument("--python", dest="python", help="Python executable; overrides the local test environment")
    parser.add_argument("--output", type=Path, help="New output directory; relative to the repository")
    parser.add_argument("--timeout", type=int, default=180, help="Seconds per test suite")
    parser.add_argument("--list", action="store_true", help="Show the plan without executing tests or writing reports")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.timeout < 1:
        parser.error("--timeout must be positive")
    try:
        python = interpreter(args.python or os.environ.get("CAPSTONE_CHECK_PYTHON"))
        if args.list:
            print(json.dumps(plan(args.area, python), ensure_ascii=False, indent=2))
            return 0
        output = args.output or Path("tools/_work/checks") / (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + args.area + "-" + uuid.uuid4().hex[:8])
        if not output.is_absolute():
            output = ROOT / output
        return execute(args.area, python, output.resolve(), args.timeout)
    except (OSError, ValueError) as error:
        print("Check failed: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
