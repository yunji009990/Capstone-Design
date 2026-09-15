"""가상 정보로 55턴을 진행해 기억·정정·삭제·초기화를 실제 Gemma에서 검사한다.

Unity, STT, TTS, 등록 API를 사용하지 않는다. 기억 정리 대기 시간과 답변 시간을
구분하고, 같은 최근 원문에서 메모리만 뺀 회상 답변도 비교한다.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Server"))
from dialogue_memory import MEMORY_VERSION, SessionMemory
from dialogue_server import Settings
from realtime_audio import Observation
from realtime_dialogue import Dialogue, build_persona
from realtime_llm import LLMClient


class TextDetector:
    def __init__(self):
        self.audio, self.pending = bytearray(), bytearray()
        self.speaking = False
    def reset(self):
        self.audio.clear()
        self.pending.clear()
        self.speaking = False


async def run(args):
    if args.output.exists():
        raise ValueError("Use a new output path")
    settings = Settings.from_env()
    llm = LLMClient(settings.normal, settings.reasoning, context_limit=settings.context_tokens)
    async def resolve_forget(payload):
        result = await llm.resolve_memory_forget(payload)
        report["deletion_decisions"].append({"payload": payload, "result": result})
        return result
    memory = SessionMemory(llm.extract_memory, resolve_forget, idle_delay=0)
    system, examples = build_persona("한국어로 대화하는 도우미. 부드러운 해요체로 간결하게 말한다.\n"
                                   "[대화 예시]\n사용자: 우리 작년에 제주도에서 먹은 초콜릿 기억나?\n"
                                   "도우미: 네, 맛있었어요.")
    events = []
    async def emit(event): events.append(event)
    dialogue = Dialogue(system, examples, None, llm, emit, detector=TextDetector(), memory=memory)
    report = {"version": MEMORY_VERSION, "started_at": datetime.now(timezone.utc).isoformat(),
              "model": settings.normal.model, "unity_used": False, "stt_used": False, "tts_used": False,
              "source_sha256": {name: hashlib.sha256((ROOT / "Server" / name).read_bytes()).hexdigest()
                                for name in ("dialogue_memory.py", "realtime_dialogue.py", "realtime_llm.py", "persona_context.py")},
              "turns": [], "checks": {}, "deletion_decisions": [], "memory_wait_seconds": 0, "complete": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    async def flush():
        started = time.monotonic()
        # 몇 턴마다 사용자가 쉬는 상황을 재현한다. 응답 지연 수치에는 포함하지 않는다.
        for _ in range(20):
            if not memory.pending:
                break
            before = memory.failures
            memory.kick(force=True)
            if memory.task:
                await asyncio.wait_for(memory.task, 30)
            if memory.failures > before:
                break
        report["memory_wait_seconds"] += round(time.monotonic() - started, 3)

    async def turn(text):
        events.clear()
        started = time.monotonic()
        await dialogue.text(text)
        await asyncio.wait_for(dialogue.active.task, 50)
        done = next((e for e in events if e["type"] == "response.done"), None)
        error = next((e for e in events if e["type"] == "error"), None)
        row = {"turn": len(report["turns"]) + 1, "user": text,
               "answer": done["text"] if done else "", "error": error,
               "seconds": round(time.monotonic() - started, 3), "memory": memory.status()}
        report["turns"].append(row)
        save()
        if error or not done:
            raise RuntimeError("Dialogue did not complete: " + str(error))
        return row["answer"]

    try:
        if not await llm.available():
            raise RuntimeError("LLM unavailable")
        fillers = ["오늘 하늘이 맑네.", "잠깐 창밖을 보고 있었어.", "여기 조용해서 대화하기 편하다.",
                   "천천히 이야기하니까 괜찮네.", "지금은 그냥 잡담하고 싶어.", "물 한 모금 마셨어."]
        anchors = {1: "내 발표는 다음 주 수요일 오후 3시야. 꼭 기억해 줘.",
                   2: "나는 커피보다 보리차를 좋아해.", 3: "우리 집 고양이 이름은 구름이야.",
                   6: "아까 발표 일정 정정할게. 다음 주 목요일 오후 4시로 변경됐어.",
                   9: "발표 시간만 오후 5시로 바꿀게."}
        for number in range(1, 56):
            await turn(anchors.get(number, fillers[number % len(fillers)]))
            if number % 3 == 0:
                await flush()
            if number % 10 == 0:
                print(json.dumps({"turn": number, "memory": memory.status()}, ensure_ascii=False), flush=True)
        await flush()
        report["before_recall"] = {"status": memory.status(), "facts": memory.fact_rows(max_chars=12000),
                                   "summary": memory.summary(), "raw_history_messages": len(dialogue.history)}
        raw_history = json.dumps(dialogue.history, ensure_ascii=False)
        report["checks"]["anchors_outside_recent_history"] = all(word not in raw_history for word in ("보리차", "구름", "목요일"))
        question = "내 발표 일정, 좋아하는 음료, 고양이 이름을 다시 알려줘."
        without_memory = Dialogue(system, examples, None, llm, emit, detector=TextDetector())
        without_memory.history = list(dialogue.history)
        report["without_memory_answer"] = "".join([piece async for piece in llm.stream(
            without_memory.messages(Observation(question, audio_event="text", language="ko")), "normal")])
        recalled = await turn(question)
        report["checks"]["recalls_corrected_facts"] = all(word in recalled for word in ("목요일", "보리차", "구름")) and ("5시" in recalled or "다섯 시" in recalled)
        report["checks"]["examples_not_stored_as_facts"] = "제주" not in json.dumps(memory.fact_rows(), ensure_ascii=False)
        deleted = await turn("발표 일정은 기억에서 지워줘. 음료 취향과 고양이 이름은 유지해줘.")
        report["checks"]["forget_acknowledged"] = "지웠어요" in deleted
        await flush()
        report["after_forget"] = {"status": memory.status(), "facts": memory.fact_rows(max_chars=12000), "summary": memory.summary()}
        remaining = " ".join(e.text for e in memory.events.values()) + json.dumps(dialogue.history, ensure_ascii=False)
        report["checks"]["deleted_fact_absent_from_sources"] = all(word not in remaining for word in ("목요일", "수요일", "오후 5시"))
        forgotten = await turn("내 발표 일정이 언제였지?")
        report["checks"]["deleted_fact_not_recalled"] = all(word not in forgotten for word in ("목요일", "수요일", "5시", "다섯 시"))
        other = await turn("내 음료 취향과 고양이 이름만 다시 알려줘.")
        report["checks"]["unrelated_facts_preserved"] = "보리차" in other and "구름" in other
        await dialogue.reset()
        report["checks"]["reset_clears_all_sources"] = not memory.events and not memory.facts and not dialogue.history
        after_reset = await turn("내 고양이 이름을 알고 있니?")
        report["checks"]["reset_does_not_recall_old_name"] = "구름" not in after_reset
        report["complete"] = True
    finally:
        await dialogue.close()
        await llm.close()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["passed"] = report["complete"] and all(report["checks"].values())
        save()
    print(json.dumps({"passed": report["passed"], "checks": report["checks"], "turns": len(report["turns"]),
                      "memory_wait_seconds": report["memory_wait_seconds"]}, ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
