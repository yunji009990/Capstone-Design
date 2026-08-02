"""설문 응답 JSON을 LLM 시스템 프롬프트로 변환.

VR 클라이언트 / 대화 엔진에서 이 함수만 호출하면 페르소나 프롬프트를 얻는다.
"""
from __future__ import annotations

from typing import Any

TONE_DESCRIPTIONS = {
    "warm_comfort": "사용자를 따듯하게 위로하고 안심시키는 어조. 슬픔을 인정하고 부드럽게 감싸는 말투를 사용한다.",
    "casual_recreation": "생전 평소 대화를 자연스럽게 재현한다. 사용자가 입력한 말투·자주 쓰던 표현을 적극 활용한다.",
    "free_dialogue": "사용자의 흐름을 따라가며 자유롭게 대화한다. 어떤 주제든 부드럽게 받아준다.",
}


def build_persona_prompt(session: dict[str, Any]) -> str:
    """`database.get_session()` 결과(또는 동일 구조 dict)를 받아 시스템 프롬프트 문자열을 만든다."""
    payload = session["payload"]
    basic = payload.get("basic", {})
    emotion = payload.get("emotion", {})
    tone_key = payload.get("tone_setting", "warm_comfort")
    tone_desc = TONE_DESCRIPTIONS.get(tone_key, TONE_DESCRIPTIONS["warm_comfort"])

    relation = basic.get("relation", "소중한 사람")
    honorific = basic.get("honorific", "")
    memories = basic.get("shared_memories", []) or []
    traits = basic.get("personality_traits", []) or []
    quirks = basic.get("speech_quirks", []) or []

    missed = emotion.get("missed_moment", "")
    unsaid = emotion.get("unsaid_words", "")
    wished = emotion.get("wished_to_hear", "")

    lines: list[str] = []
    lines.append("당신은 사용자가 그리워하는 사람의 모습을 따듯하게 재현하는 대화 페르소나입니다.")
    lines.append("이것은 실제 고인의 부활이 아니라, 사용자의 기억과 추억을 바탕으로 한 위로의 재현임을 인지하고 행동하세요.")
    lines.append("")
    lines.append(f"[관계] 사용자와 당신의 관계: {relation}")
    if honorific:
        lines.append(f"[호칭] 사용자는 당신을 '{honorific}'(이)라고 부릅니다. 당신도 그에 어울리는 호칭과 말투를 사용하세요.")
    if traits:
        lines.append(f"[성격] " + ", ".join(traits))
    if quirks:
        lines.append("[말투] 자주 쓰던 표현·말버릇을 자연스럽게 섞어 사용하세요: "
                     + " / ".join(f'"{q}"' for q in quirks))
    if memories:
        lines.append("[함께한 추억] 다음 기억을 대화 맥락으로 활용하되, 사용자가 먼저 꺼내지 않은 추억은 강요하지 마세요:")
        for m in memories:
            lines.append(f"  - {m}")
    lines.append("")
    lines.append(f"[대화 톤] {tone_desc}")
    lines.append("")
    if missed or unsaid or wished:
        lines.append("[사용자의 마음]")
        if missed:
            lines.append(f"  - 가장 그리운 순간: {missed}")
        if unsaid:
            lines.append(f"  - 못다 한 말: {unsaid}")
        if wished:
            lines.append(f"  - 듣고 싶은 말: {wished}")
        lines.append("  → 사용자가 이 마음을 자연스럽게 꺼낼 수 있도록 부드럽게 길을 내어 주세요. "
                     "다만 사용자가 준비되지 않았다면 절대 재촉하지 마세요.")
    lines.append("")
    lines.append("[지침]")
    lines.append("- 짧고 따듯한 문장으로 답하세요. 한 번에 2~3문장이면 충분합니다.")
    lines.append("- 사용자의 감정을 먼저 인정하고, 그 다음에 응답하세요.")
    lines.append("- 추측·창작은 최소화하고, 설문에 주어진 사실 범위 안에서 일관되게 행동하세요.")
    lines.append("- 사용자가 위험 신호(자해·극단적 선택 등)를 보이면 즉시 대화 페르소나를 잠시 내려놓고, "
                 "전문 상담 자원(1393 자살예방상담전화 등)으로 연결할 것을 안내하세요.")
    return "\n".join(lines)


def build_persona_summary(session: dict[str, Any]) -> str:
    """관리자 페이지에서 한눈에 보여줄 짧은 요약."""
    payload = session["payload"]
    basic = payload.get("basic", {})
    rel = basic.get("relation", "?")
    hon = basic.get("honorific", "")
    tone = payload.get("tone_setting", "?")
    return f"{rel}" + (f" ({hon})" if hon else "") + f" · 톤={tone}"
