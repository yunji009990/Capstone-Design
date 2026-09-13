"""Text-only evaluation of the production routing and interruption classifiers.

Uses the running LLM endpoint. Does not start STT, TTS, or an answer generation.
Expected decisions are defined in a versioned fixture before the model runs.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "Server" if (ROOT / "Server").is_dir() else ROOT
sys.path.insert(0, str(SERVER))
from dialogue_server import Settings
from realtime_audio import Observation
from realtime_dialogue import Dialogue
from realtime_llm import LLMClient


def build_input(case, contexts):
    context = copy.deepcopy(contexts[case["context"]])
    # Reuse the real post-STT message builder without constructing an audio
    # frontend or sending a response. The detector stub is never used.
    dialogue = Dialogue("너는 친절한 한국어 대화 도우미다.", [], None, None, None, detector=object())
    dialogue.history = context.get("history", [])
    unity = context.get("unity", {})
    if unity.get("current_state"):
        dialogue.context.update({"kind": "state", "text": unity["current_state"]})
    for event in unity.get("recent_actions", []):
        dialogue.context.update({"kind": "action", "text": event["description"]})
    observation = Observation(case["text"], emotion=case.get("emotion", "unknown"),
                              audio_event="text", language="ko")
    return dialogue.messages(observation), context.get("pending")


def percentile(values, percent):
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered)-1, int((len(ordered)-1)*percent)))] if ordered else None


def summarize(rows):
    latency = [row["seconds"] for row in rows]
    groups = {}
    for kind in ("route", "interrupt"):
        subset = [row for row in rows if row["kind"] == kind]
        if not subset: continue
        groups[kind] = {"passed": sum(row["passed"] for row in subset), "total": len(subset),
                        "errors": sum(bool(row["error"]) for row in subset)}
        if kind == "interrupt":
            groups[kind]["action_matched"] = sum(row["actual"].get("action") == row["expected"]["action"] for row in subset)
        groups[kind]["route_matched"] = sum(row["actual"].get("route") == row["expected"]["route"] for row in subset)
    return {"passed": sum(row["passed"] for row in rows), "total": len(rows), "groups": groups,
            "latency_seconds": {"median": round(statistics.median(latency), 3) if latency else None,
                                "p95": percentile(latency, .95), "max": max(latency) if latency else None}}


async def run(args):
    source = args.cases.read_bytes()
    fixture = json.loads(source.decode("utf-8"))
    cases = [case for case in fixture["cases"] if args.split == "all" or case["split"] == args.split]
    if args.ids:
        selected = set(args.ids.split(","))
        cases = [case for case in cases if case["id"] in selected]
        if selected != {case["id"] for case in cases}: raise ValueError("Unknown case IDs or wrong split")
    if not cases or len({case["id"] for case in cases}) != len(cases): raise ValueError("Empty or duplicate cases")
    settings = Settings.from_env()
    llm = LLMClient(settings.normal, settings.reasoning)
    report = {
        "time": datetime.now(timezone.utc).isoformat(), "split": args.split,
        "fixture_sha256": hashlib.sha256(source).hexdigest(), "stt_called": False, "tts_called": False,
        "answer_generated": False, "model": settings.normal.model,
        "normal_extra": settings.normal.extra, "reasoning_configured": settings.reasoning is not None,
        "source_sha256": {name: hashlib.sha256((SERVER/name).read_bytes()).hexdigest()
                          for name in ("interruption_policy.py", "realtime_llm.py", "realtime_dialogue.py")},
        "results": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not await llm.available(): raise RuntimeError("Configured LLM is unavailable")
        if settings.reasoning is None: raise RuntimeError("Configure reasoning before evaluating its route")
        for case in cases:
            messages, pending = build_input(case, fixture["contexts"])
            expected = {key: case[key] for key in ("action", "route") if key in case}
            row = dict(id=case["id"], split=case["split"], kind=case["kind"], text=case["text"],
                       context=case["context"], expected=expected, actual={}, error=None)
            started = time.monotonic()
            try:
                if case["kind"] == "route":
                    row["actual"] = {"route": await asyncio.wait_for(llm.route(messages), 9)}
                elif case["kind"] == "interrupt" and pending is not None:
                    decision = await asyncio.wait_for(llm.decide_interruption(messages, pending), 6.5)
                    row["actual"] = dict(action=decision.action, route=decision.route, reason=decision.reason)
                else:
                    raise ValueError("Invalid case kind or missing suspended answer")
            except Exception as error:
                row["error"] = type(error).__name__ + ": " + str(error)
            row["seconds"] = round(time.monotonic()-started, 3)
            row["passed"] = row["error"] is None and all(row["actual"].get(k) == v for k, v in expected.items())
            report["results"].append(row)
            if not row["passed"]: print(json.dumps(row, ensure_ascii=False), flush=True)
            elif len(report["results"]) % 10 == 0: print("completed", len(report["results"]), "/", len(cases), flush=True)
            # Keep a usable report even if a later request or the process fails.
            report["summary"] = summarize(report["results"])
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        await llm.close()
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    return 0 if report["summary"]["passed"] == len(cases) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("turn_judge_cases.json"))
    parser.add_argument("--split", choices=("core", "holdout", "all"), default="core")
    parser.add_argument("--ids", help="Comma-separated case IDs, for a targeted rerun")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__": main()
