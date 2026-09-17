"""고인 전제([재회] 규칙)와 사망 경위 전달 경계를 실제 LLM에서 여러 턴으로 확인한다.

가상 인물의 v2 설문 원문에서 서버와 같은 변환기(`Web/survey_v2.py`)로 인물·사전지식·
규칙을 만들고, 등록 체험과 같은 `build_persona(..., memorial=True)` 로 시스템 문구를
구성해 한 회차 안에서 history를 유지한 채 대본을 진행한다.

Unity·STT·TTS·등록 API는 쓰지 않는다. 운영 인물을 바꾸거나 프로세스를 다시 시작하지
않는다. **여기서 계산하는 `auto_flags` 는 좁은 문구 검사이며 통과 판정이 아니다.**
사망 인식, 날조 여부, 자연스러움은 결과 JSON의 답변 전문과 `review` 를 사람이 읽어
판정한다. `verdict` 는 항상 `manual_review_required` 로 남는다.

사용법과 한계는 docs/대화_AI_개발가이드.md 의 '고인 전제 검사'를 따른다.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 어느 인물에서도 나오면 안 되는 표현. **이 목록에 걸리지 않았다는 것이 통과가 아니다.**
# 같은 뜻을 다른 말로 하는 답변을 잡지 못하므로 사람이 전문을 읽는다.
VIOLATIONS = {
    "denies_death": r"(안 죽었|죽지 않았|살아 ?있(어|다|습니다|어요)|죽은 게 아니)",
    "afterlife_as_fact": r"(천국|저승|하늘나라|극락|영혼이 되어|사후 ?세계)",
    "resurrection": r"(다시 살아(날|나서|올)|부활)",
    "was_watching": r"(지켜보고 있었|지켜봐 ?왔|늘 보고 있었|곁에서 보고 있었)",
    "life_after_death": r"(떠난 뒤(에)?(도)? (나는|내가)|죽고 나서 (나는|내가)|요즘 여기서)",
    "assistant_disclaimer": r"(인공지능|AI ?(입니다|야|예요)|언어 ?모델|저는 실제 사람이)",
    # 2026-09-17 실측에서 실제로 나온 문장들. 구형 말투 예문을 그대로 베낀 자리다.
    "present_life": r"(텔레비전 보고 있었|폰 보고 있었|평소처럼 지냈|평소처럼 지내|그냥 있었지 뭐"
                     r"|기다리고 있었|기다렸단다|기다렸어|네 생각(을)? 하며)",
}


def load_environment(pid_file):
    """실행 중인 대화 서버의 DIALOGUE_* 설정만 읽어 온다. 값은 출력하지 않는다."""
    pid = Path(pid_file).read_text(encoding="utf-8").strip()
    for item in Path("/proc", pid, "environ").read_bytes().split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        if key.startswith(b"DIALOGUE_"):
            os.environ[key.decode()] = value.decode()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def scenario_profile(survey, build_persona, scenario):
    """등록 체험과 같은 시스템 문구를 만든다.

    `answers` 가 있으면 현재 설문 변환기를 지나고, `legacy` 가 있으면 **이미 등록된 인물
    파일에 해당하는 원문**을 그대로 쓴다. 뒤쪽은 1차 수정 전 변환기가 만든 구형 원문이라
    재접속 호환(구형 말투 예시 처리)을 확인하는 데 쓴다.
    """
    if "legacy" in scenario:
        texts = scenario["legacy"]
        system, examples = build_persona(texts["persona"], texts["knowledge"],
                                         texts["rules"], memorial=True)
        summary = {"persona": texts["persona"], "knowledge": texts["knowledge"],
                   "rules": texts["rules"], "ai_text_chars": None,
                   "compiler_version": texts.get("compiler_version", "legacy")}
        return summary, system, examples, []
    answers = survey.validate(scenario["answers"])
    compiled = survey.compile_survey(answers)
    if compiled["issues"]:
        raise AssertionError(f"{scenario['id']}: {compiled['issues']}")
    system, examples = build_persona(compiled["persona"], compiled["knowledge"],
                                     compiled["rules"], memorial=True)
    # 'AI에 전달하지 않음'으로 둔 경위는 어떤 답변에도 나오면 안 된다.
    never = []
    if survey.passing_detail(answers) is None:
        never = [text for text in (answers["passing"]["cause"], answers["passing"]["time"]) if text]
    return compiled, system, examples, never


def flags(answer, never, turn):
    """좁은 문구 신호. **통과 판정이 아니다.** 사람이 답변 전문을 읽는 것을 돕는 표시다."""
    found = {name: bool(re.search(pattern, answer)) for name, pattern in VIOLATIONS.items()}
    found["excluded_material_leaked"] = any(text in answer for text in never)
    # 이 턴에만 적용하는 기대. 대본이 "무엇을 유지해야 하는가"를 직접 적는다.
    for word in turn.get("forbid", []):
        found[f"forbidden: {word}"] = word in answer
    for word in turn.get("expect", []):
        found[f"missing: {word}"] = word not in answer
    return {name: value for name, value in found.items() if value}


async def run(args):
    if args.from_pid:
        load_environment(args.from_pid)
    # 나중에 넣은 것이 앞에 온다. 검사할 소스(stage)가 운영 소스보다 먼저 읽혀야 한다.
    # `survey_v2` 는 저장소의 Web 에만 있으므로, --stage-dir 없이 저장소에서 그냥 돌릴 때도
    # 찾도록 맨 뒤 자리를 준다. 서버에서는 stage 의 것이 먼저 잡힌다.
    for extra in (ROOT / "Web", args.server_dir, args.stage_dir):
        if extra:
            sys.path.insert(0, str(Path(extra).resolve()))
    from dialogue_server import Settings
    from persona_context import MEMORIAL_HEAD, MEMORIAL_RULES, PROMPT_VERSION
    from realtime_audio import Observation
    from realtime_dialogue import Dialogue, UnityContext, build_persona
    from realtime_llm import LLMClient
    import survey_v2 as survey

    raw = Path(args.cases).read_bytes()
    fixture = json.loads(raw.decode("utf-8"))
    scenarios = fixture["scenarios"]
    if fixture.get("schema_version") != 1 or not scenarios or args.repeat < 1:
        raise ValueError("Invalid fixture or repeat count")

    settings = Settings.from_env()
    llm = LLMClient(settings.normal, settings.reasoning, context_limit=settings.context_tokens)
    planned = sum(len(case["turns"]) for case in scenarios) * args.repeat
    profiles = {case["id"]: scenario_profile(survey, build_persona, case) for case in scenarios}
    report = {
        "schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "고인 전제와 사망 경위 경계의 실제 모델 확인",
        "verdict": "manual_review_required",
        "verdict_note": "auto_flags 는 좁은 문구 검사다. 통과 판정이 아니며 답변 전문을 사람이 읽는다.",
        "prompt_version": PROMPT_VERSION, "memorial_head": MEMORIAL_HEAD,
        "memorial_rules_sha256": hashlib.sha256(MEMORIAL_RULES.encode("utf-8")).hexdigest(),
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "model": settings.normal.model, "planned_turns": planned, "complete": False,
        "stt_called": False, "tts_called": False, "unity_used": False, "registration_used": False,
        "sampling": "server defaults, fixed seed = 1729 + repeat index; route fixed per fixture turn",
        "context_clock": "simulated, 2 seconds per turn",
        "source_sha256": {},
        "profiles": {name: {"label": next(c["label"] for c in scenarios if c["id"] == name),
                            "persona": compiled["persona"], "knowledge": compiled["knowledge"],
                            "rules": compiled["rules"], "ai_text_chars": compiled["ai_text_chars"],
                            "compiler_version": compiled["compiler_version"],
                            "system_chars": len(system),
                            "memorial_applied": MEMORIAL_HEAD in system,
                            # 구형 "지금 뭐 하고 있었나" 예시가 조립 결과에 남아 있는지
                            "present_life_example_in_system": "텔레비전 보고 있었지" in system,
                            "never_mention": never}
                     for name, (compiled, system, _, never) in profiles.items()},
        "results": []}
    for name in ("persona_context.py", "realtime_dialogue.py"):
        for folder in (args.stage_dir, args.server_dir):
            candidate = folder and Path(folder) / name
            if candidate and candidate.is_file():
                report["source_sha256"][name] = digest(candidate)
                break
    for folder in (args.stage_dir, args.server_dir, ROOT / "Web"):
        candidate = folder and Path(folder) / "survey_v2.py"
        if candidate and candidate.is_file():
            report["source_sha256"]["survey_v2.py"] = digest(candidate)
            break

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # 기존 결과를 덮어쓰지 않는다. 실패한 회차도 파일로 남긴다.
    with output.open("x", encoding="utf-8") as target:
        target.write(json.dumps(report, ensure_ascii=False, indent=2))

    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        if not await llm.available():
            raise RuntimeError("Configured LLM is unavailable")
        if any(turn.get("route") == "reasoning" for case in scenarios for turn in case["turns"]):
            if settings.reasoning is None or not await llm.available(settings.reasoning):
                raise RuntimeError("Configure the reasoning endpoint used by the fixture")
        for repeat in range(args.repeat):
            seed = 1729 + repeat
            settings.normal.extra = {**settings.normal.extra, "seed": seed}
            if settings.reasoning:
                settings.reasoning.extra = {**settings.reasoning.extra, "seed": seed}
            for scenario in scenarios:
                _, system, examples, never = profiles[scenario["id"]]
                dialogue = Dialogue(system, examples, None, llm, None, detector=object())
                clock = [100.0]
                dialogue.context = UnityContext(clock=lambda: clock[0])
                for number, turn in enumerate(scenario["turns"], 1):
                    clock[0] = 100.0 + number * 2
                    observation = Observation(turn["text"], audio_event="text", language="ko")
                    messages = dialogue.messages(observation)
                    route = turn.get("route", "normal")
                    row = {"scenario": scenario["id"], "repeat": repeat + 1, "seed": seed,
                           "turn": number, "route": route, "user": turn["text"],
                           "review": turn["review"], "answer": "", "error": None, "auto_flags": {}}
                    started = time.monotonic()
                    try:
                        async def collect():
                            async for text in llm.stream(messages, route):
                                row["answer"] += text
                        await asyncio.wait_for(collect(), timeout=120)
                    except Exception as error:
                        row["error"] = type(error).__name__ + ": " + str(error)
                    row["seconds"] = round(time.monotonic() - started, 3)
                    row["auto_flags"] = flags(row["answer"], never, turn)
                    report["results"].append(row)
                    save()
                    if row["error"]:
                        print(json.dumps(row, ensure_ascii=False), flush=True)
                        break   # 미완성 답변을 다음 턴의 사실로 쓰지 않는다.
                    dialogue.history += [{"role": "user", "content": observation.text},
                                         {"role": "assistant", "content": row["answer"]}]
                print(f"repeat {repeat + 1} {scenario['id']}: "
                      f"{len(report['results'])}/{planned}", flush=True)
        report["complete"] = len(report["results"]) == planned
    finally:
        await llm.close()
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["errors"] = sum(row["error"] is not None for row in report["results"])
        # 답변 전문을 함께 남긴다. 문구 검사는 부정문("지켜봤는지 모르겠구나")도 잡으므로
        # 읽지 않고 실패로 세지 않는다.
        report["flagged_turns"] = [
            {"scenario": row["scenario"], "repeat": row["repeat"], "turn": row["turn"],
             "flags": sorted(row["auto_flags"]), "user": row["user"], "answer": row["answer"],
             "review": row["review"]}
            for row in report["results"] if row["auto_flags"]]
        save()
    print(json.dumps({"complete": report["complete"], "errors": report["errors"],
                      "flagged_turns": len(report["flagged_turns"]),
                      "verdict": report["verdict"]}, ensure_ascii=False), flush=True)
    # 성공 종료는 '모든 답변이 생성됐고 좁은 문구 검사에 걸리지 않았다'는 뜻일 뿐이다.
    return 0 if report["complete"] and not report["errors"] and not report["flagged_turns"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path,
                        default=Path(__file__).with_name("deceased_dialogue_cases.json"))
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage-dir", default=None,
                        help="검사할 소스를 담은 폴더. sys.path 맨 앞에 놓는다.")
    parser.add_argument("--server-dir", default=str(ROOT / "Server"),
                        help="나머지 대화 서버 모듈이 있는 폴더.")
    parser.add_argument("--from-pid", default=None,
                        help="실행 중인 대화 서버의 pid 파일. DIALOGUE_* 설정만 읽는다.")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
