"""실제 대화 WebSocket에서 되물음 뒤 단답의 기억·정정·장소 구분을 검사한다.

인증된 테스트 연결과 가상 정보만 사용한다. 강제 memory.flush 없이 자동 정리가
끝날 시간을 주고, 34턴의 다른 대화 뒤 최근 원문 밖에 있는 정보를 회상한다.
실행 환경에는 websockets가 필요하다. 운영 대화 Python 환경에 설치된 패키지를 쓸 수 있다.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.request
from urllib.parse import urlsplit, urlunsplit

import websockets


PERSONA = "한국어로 대화하는 도우미. 부드러운 해요체로 간결하게 말한다."
FILLERS = ["잠깐 쉬고 있어.", "지금은 천천히 대화하고 싶어.", "창문을 조금 열었어.",
           "여기 조용하네.", "물 한 모금 마셨어.", "잠시 스트레칭했어."]
CASES = [
    {"id": "travel_same_batch", "title": "같은 정리 묶음의 여행지 단답", "warmup": 0,
     "setup": [("question", "지난번 가족 여행을 어디로 갔었지?"), ("answer", "제주도.")],
     "recall": "지난번 가족 여행지가 어디였지?", "expected": ["제주도"],
     "bindings": [(["여행"], "제주도")]},
    {"id": "travel_across_batch", "title": "질문 뒤 정리가 끝난 다음의 여행지 단답", "warmup": 2,
     "setup": [("question", "지난번 가족 여행을 어디로 갔었지?"), ("answer", "제주도.")],
     "recall": "지난번 가족 여행지가 어디였지?", "expected": ["제주도"],
     "bindings": [(["여행"], "제주도")]},
    {"id": "weekday_correction", "title": "요일 단답과 짧은 정정", "warmup": 2,
     "setup": [("question", "내 다음 발표는 무슨 요일이지?"), ("answer", "목요일.")],
     "recall": "내 다음 발표는 무슨 요일이지?", "initial_expected": ["목요일"],
     "correction": "아니, 금요일이야.", "expected": ["금요일"], "superseded": ["목요일"],
     "bindings": [(["발표"], "금요일")]},
    {"id": "two_places", "title": "여행지와 고향의 장소 단답 구분", "warmup": 0,
     "setup": [("question", "지난번 가족 여행을 어디로 갔었지?"), ("answer", "통영."),
               ("question", "내 고향이 어디야?"), ("answer", "속초.")],
     "recall": "지난번 가족 여행지와 내 고향을 각각 알려줘.", "expected": ["통영", "속초"],
     "bindings": [(["여행"], "통영"), (["고향", "출신"], "속초")]},
    {"id": "travel_without_rehearsal", "title": "즉시 재질문 없이 여행지 단답을 기억", "warmup": 2,
     "setup": [("question", "지난번 가족 여행을 어디로 갔었지?"), ("answer", "제주도.")],
     "skip_immediate": True, "recall": "지난번 가족 여행지가 어디였지?", "expected": ["제주도"],
     "bindings": [(["여행"], "제주도")]},
]


def health_url(url):
    parsed = urlsplit(url)
    return urlunsplit(("https" if parsed.scheme == "wss" else "http", parsed.netloc, "/health", "", ""))


def health(url):
    return json.load(urllib.request.urlopen(health_url(url), timeout=10))


def contains(answer, values):
    return all(value in answer for value in values)


def source_has_binding(snapshot, labels, value):
    return any(any(label in fact["key"] for label in labels)
               and any(source["role"] == "user" and value in source["text"] for source in fact["sources"])
               for fact in snapshot["facts"])


async def receive(ws, expected):
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        try:
            event = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        except asyncio.TimeoutError:
            await ws.send(json.dumps({"type": "ping"}))
            continue
        if event["type"] == "error":
            raise RuntimeError("Dialogue error: " + event["code"])
        if event["type"] == expected:
            return event
    raise TimeoutError("Missing event: " + expected)


async def snapshot(ws):
    await ws.send(json.dumps({"type": "memory.inspect"}))
    return await receive(ws, "memory.snapshot")


async def settle(ws):
    # 운영 기본 지연 0.5초보다 조금 더 기다린다. 강제 정리는 사용하지 않는다.
    await asyncio.sleep(.6)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        result = await snapshot(ws)
        if not result["status"]["busy"]:
            return result
        await asyncio.sleep(.15)
    raise TimeoutError("Memory job did not finish")


async def run_case(args, case, report, save, options):
    row = {"id": case["id"], "title": case["title"], "definition": case,
           "turns": [], "checks": {}, "complete": False}
    report["cases"].append(row)
    save()
    async with websockets.connect(args.url, **options) as ws:
        await ws.send(json.dumps({"type": "start", "protocol": 1, "sample_rate": 16000,
            "channels": 1, "format": "pcm_s16le", "test_mode": True,
            "test_persona": PERSONA, "interruption_policy": "semantic_v1"}, ensure_ascii=False))
        row["ready"] = await receive(ws, "ready")
        if not row["ready"]["memory"]["enabled"] or row["ready"]["tts"]:
            raise RuntimeError("Expected connection memory with TTS disabled")

        async def turn(text, phase):
            started = time.monotonic()
            await ws.send(json.dumps({"type": "text", "text": text}, ensure_ascii=False))
            done = await receive(ws, "response.done")
            elapsed = round(time.monotonic() - started, 3)
            wait_started = time.monotonic()
            memory = await settle(ws)
            entry = {"turn": len(row["turns"]) + 1, "phase": phase, "user": text,
                     "answer": done["text"], "response_id": done["response_id"],
                     "answer_seconds": elapsed, "settle_seconds": round(time.monotonic() - wait_started, 3),
                     "memory": memory}
            row["turns"].append(entry)
            save()
            if len(row["turns"]) % 8 == 0:
                print(json.dumps({"case": case["id"], "turn": len(row["turns"]),
                    "memory": memory["status"]}, ensure_ascii=False), flush=True)
            return entry

        for number in range(case["warmup"]):
            await turn(FILLERS[number], "warmup")
        questions = []
        for phase, text in case["setup"]:
            entry = await turn(text, phase)
            if phase == "question": questions.append(entry)
        # 판정 문자열 외에 실제 질문을 보고 의미가 맞는지도 보고서에서 검토한다.
        row["checks"]["asked_for_missing_information"] = all(
            bool(re.search(r"[?？]|알려.{0,12}(?:주|줄)", q["answer"])) for q in questions)
        row["checks"]["target_values_not_given_by_ai_before_user"] = not any(
            value in questions[0]["answer"] for value in case.get("initial_expected", case["expected"]))
        if case["warmup"] == 2:
            row["checks"]["question_processed_before_short_answer"] = questions[0]["memory"]["status"]["updates"] > 0
        if not case.get("skip_immediate"):
            immediate = await turn(case["recall"], "immediate_recall")
            row["checks"]["immediate_recall"] = contains(immediate["answer"], case.get("initial_expected", case["expected"]))
        if case.get("correction"):
            await turn(case["correction"], "correction")
            corrected = await turn(case["recall"], "corrected_recall")
            row["checks"]["immediate_correction"] = contains(corrected["answer"], case["expected"]) and not any(
                value in corrected["answer"] for value in case["superseded"])

        for number in range(args.gap_turns):
            await turn(FILLERS[number % len(FILLERS)], "gap")
        recent = row["turns"][-30:]
        recent_text = " ".join(entry["user"] + " " + entry["answer"] for entry in recent)
        anchors = case["expected"] + case.get("superseded", [])
        row["checks"]["values_outside_recent_30_turns"] = all(value not in recent_text for value in anchors)
        row["before_delayed_recall"] = await snapshot(ws)
        row["checks"]["user_evidence_bound_to_correct_topic"] = all(
            source_has_binding(row["before_delayed_recall"], labels, value) for labels, value in case["bindings"])
        delayed = await turn(case["recall"], "delayed_recall")
        row["checks"]["delayed_recall"] = contains(delayed["answer"], case["expected"])
        if case.get("superseded"):
            row["checks"]["old_value_not_recalled"] = not any(value in delayed["answer"] for value in case["superseded"])
        row["checks"]["no_extraction_failures"] = delayed["memory"]["status"]["failures"] == 0
        row["checks"]["no_capacity_loss"] = delayed["memory"]["status"]["dropped"] == 0
        row["complete"] = True
        row["passed"] = all(row["checks"].values())
        save()
        print(json.dumps({"case": case["id"], "passed": row["passed"], "checks": row["checks"],
                          "delayed_answer": delayed["answer"]}, ensure_ascii=False), flush=True)
        await ws.send(json.dumps({"type": "stop"}))
        await receive(ws, "stopped")


async def run(args):
    if args.output.exists(): raise ValueError("Use a new output file")
    token = os.environ.get("DIALOGUE_TOKEN", "")
    if not token: raise ValueError("DIALOGUE_TOKEN must be set without putting it on the command line")
    header = "additional_headers" if "additional_headers" in inspect.signature(websockets.connect).parameters else "extra_headers"
    options = {header: {"X-Token": token}}
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "cases": [],
              "persona": PERSONA, "gap_turns": args.gap_turns, "force_flush_used": False,
              "unity_used": False, "stt_used": False, "tts_used": False,
              "protocol": "authenticated_test_websocket", "complete": False,
              "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    if args.source_dir:
        report["server_source_sha256"] = {name: hashlib.sha256((args.source_dir/name).read_bytes()).hexdigest()
            for name in ("dialogue_server.py", "dialogue_memory.py", "realtime_dialogue.py", "realtime_llm.py", "persona_context.py")}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def save(): args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        report["health_before"] = health(args.url)
        if report["health_before"]["connections"] or report["health_before"]["status"] != "ready":
            raise RuntimeError("Dialogue server must be idle and ready")
        selected = [case for case in CASES if not args.case or case["id"] == args.case]
        for case in selected:
            await run_case(args, case, report, save, options)
        report["complete"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["passed"] = report["complete"] and all(case.get("passed", False) for case in report["cases"])
        try:
            report["health_after"] = health(args.url)
        except Exception as error:
            report["health_after_error"] = type(error).__name__
            report["passed"] = False
        save()
    print(json.dumps({"passed": report["passed"], "cases": len(report["cases"]),
        "turns": sum(len(case["turns"]) for case in report["cases"]), "output": str(args.output)}), flush=True)
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="ws://127.0.0.1:8002/dialogue")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--gap-turns", type=int, default=34)
    parser.add_argument("--case", choices=[case["id"] for case in CASES])
    args = parser.parse_args()
    if args.gap_turns < 31: parser.error("--gap-turns must be at least 31")
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
