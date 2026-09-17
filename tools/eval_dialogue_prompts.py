"""가상 인물의 여러 턴 대본으로 구형/새 프롬프트를 실제 LLM에서 비교한다.

Unity·STT·TTS·등록 API는 사용하지 않는다. 답변 경로와 생성 설정을 고정하고
프롬프트 규칙, 예시 배치의 영향을 단계별로 비교한다. 결과의 문구 검사는
제한된 사실 검사이며 자연스러움/허위 기억은 전체 대화를 읽어 판정해야 한다.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Server"))
from dialogue_server import Settings
from persona_context import BASE_RULES, LEGACY_BASE_RULES, PROMPT_VERSION, _split_examples, additional_rules
from realtime_audio import Observation
from realtime_dialogue import Dialogue, UnityContext, build_persona
from realtime_llm import LLMClient


def build_profile(scenario, variant):
    persona = scenario["persona"]
    knowledge = scenario.get("knowledge", "")
    rules = scenario.get("rules", "")
    if scenario.get("legacy_rules"):
        rules = LEGACY_BASE_RULES + ("\n\n" + rules if rules else "")
    if variant == "current":
        return build_persona(persona, knowledge, rules)
    if scenario["profile"] == "test":
        return (persona.strip(), []) if variant == "legacy" else build_persona(persona)
    persona, examples = _split_examples(persona)
    # 구형 분리 함수의 안내 문구도 재현한다. 실제 운영 파일을 불러오지 않는다.
    persona = "\n".join(
        "[예시 사용법] 앞서 나눈 대화는 말투의 본보기입니다. 상황에 맞는 문장을 새로 만들어 말합니다."
        if line.startswith("[예시 사용법]") else line for line in persona.splitlines())
    selected_rules = (rules or LEGACY_BASE_RULES) if variant == "legacy" else BASE_RULES
    system = f"{selected_rules}\n\n[인물]\n{persona}"
    if knowledge:
        system += f"\n\n[사전지식]\n{knowledge}"
    if variant == "rules_only" and additional_rules(rules):
        system += f"\n\n[인물별 추가 규칙]\n{additional_rules(rules)}"
    return system, examples


def summary(rows):
    result = {}
    for variant in sorted({row["variant"] for row in rows}):
        group = [row for row in rows if row["variant"] == variant]
        checked = [row for row in group if row["checks"]]
        result[variant] = {
            "turns": len(group), "errors": sum(row["error"] is not None for row in group),
            "pattern_checked_turns": len(checked),
            "pattern_passed_turns": sum(not row["error"] and all(row["checks"].values()) for row in checked),
            "median_chars": statistics.median(len(row["answer"]) for row in group),
            "question_turns": sum("?" in row["answer"] or "？" in row["answer"] for row in group),
            "median_seconds": round(statistics.median(row["seconds"] for row in group), 3)}
    return result


async def run(args):
    raw = args.cases.read_bytes()
    fixture = json.loads(raw.decode("utf-8"))
    scenarios = fixture["scenarios"]
    if fixture.get("schema_version") != 1 or not scenarios or args.repeat < 1:
        raise ValueError("Invalid fixture or repeat count")
    variants = args.variants.split(",")
    if not variants or len(set(variants)) != len(variants) or set(variants) - {"legacy", "rules_only", "current"}:
        raise ValueError("Unknown or duplicate variants")
    settings = Settings.from_env()
    llm = LLMClient(settings.normal, settings.reasoning)
    planned = sum(len(case["turns"]) for case in scenarios) * len(variants) * args.repeat
    report = {"schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(),
              "prompt_version": PROMPT_VERSION, "fixture_sha256": hashlib.sha256(raw).hexdigest(),
              "model": settings.normal.model, "planned_turns": planned, "complete": False,
              "stt_called": False, "tts_called": False, "unity_used": False, "registration_used": False,
              "sampling": "server defaults, paired seed = 1729 + repeat index; route fixed per fixture",
              "context_clock": "simulated, 2 seconds per turn",
              "token_budgets": {"normal": settings.normal.max_tokens,
                                "reasoning": settings.reasoning.max_tokens if settings.reasoning else None},
              "source_sha256": {name: hashlib.sha256((ROOT / "Server" / name).read_bytes()).hexdigest()
                                for name in ("persona_context.py", "realtime_dialogue.py", "realtime_llm.py")},
              "results": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # 기존 비교 결과를 덮어쓰지 않는다. 도중 실패해도 완료 수와 답변을 보존한다.
    with args.output.open("x", encoding="utf-8") as target:
        target.write(json.dumps(report, ensure_ascii=False, indent=2))
    try:
        if not await llm.available():
            raise RuntimeError("Configured LLM is unavailable")
        if any(turn.get("route") == "reasoning" for case in scenarios for turn in case["turns"]):
            if settings.reasoning is None or not await llm.available(settings.reasoning):
                raise RuntimeError("Configure the reasoning endpoint used by the fixture")
        for repeat in range(args.repeat):
            settings.normal.extra = {**settings.normal.extra, "seed": 1729 + repeat}
            if settings.reasoning:
                settings.reasoning.extra = {**settings.reasoning.extra, "seed": 1729 + repeat}
            order = variants if repeat % 2 == 0 else list(reversed(variants))
            for scenario in scenarios:
                for variant in order:
                    system, examples = build_profile(scenario, variant)
                    dialogue = Dialogue(system, examples, None, None, None, detector=object())
                    simulated_time = [100.0]
                    dialogue.context = UnityContext(clock=lambda: simulated_time[0])
                    for number, turn in enumerate(scenario["turns"], 1):
                        simulated_time[0] = 100.0 + number * 2
                        if turn.get("context"):
                            dialogue.context.update(turn["context"])
                        observation = Observation(turn["text"], audio_event="text", language="ko")
                        messages = dialogue.messages(observation)
                        route = turn.get("route", "normal")
                        row = {"scenario": scenario["id"], "variant": variant, "repeat": repeat + 1,
                               "turn": number, "user": turn["text"], "route": route,
                               "review": turn["review"], "answer": "", "error": None, "checks": {}}
                        started = time.monotonic()
                        try:
                            async def collect():
                                async for text in llm.stream(messages, route):
                                    row["answer"] += text
                            await asyncio.wait_for(collect(), timeout=60)
                        except Exception as error:
                            row["error"] = type(error).__name__ + ": " + str(error)
                        row["seconds"] = round(time.monotonic() - started, 3)
                        for pattern in turn.get("all_patterns", []):
                            row["checks"]["contains: " + pattern] = bool(re.search(pattern, row["answer"]))
                        for pattern in turn.get("none_patterns", []):
                            row["checks"]["excludes: " + pattern] = not re.search(pattern, row["answer"])
                        report["results"].append(row)
                        report["summary"] = summary(report["results"])
                        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                        if row["error"]:
                            print(json.dumps(row, ensure_ascii=False), flush=True)
                            break  # 미완성 답변을 다음 턴의 사실로 사용하지 않는다.
                        # TTS 없이 전체 텍스트가 정상 전달됐을 때와 같은 기록이다.
                        dialogue.history += [{"role": "user", "content": observation.text},
                                             {"role": "assistant", "content": row["answer"]}]
                    print(f"repeat {repeat + 1}, {scenario['id']}, {variant}: {len(report['results'])}/{planned}", flush=True)
        report["complete"] = len(report["results"]) == planned
    finally:
        await llm.close()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report.get("summary", {}), ensure_ascii=False), flush=True)
    # 품질 판정은 별도 검토다. 여기의 성공은 모든 답변이 정상 생성되었다는 뜻이다.
    return 0 if report["complete"] and not any(row["error"] for row in report["results"]) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("dialogue_prompt_cases.json"))
    parser.add_argument("--variants", default="legacy,rules_only,current")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
