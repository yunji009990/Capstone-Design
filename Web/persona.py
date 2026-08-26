"""설문 응답을 페르소나·사전지식으로 바꾼다.

서버는 [공통 규칙] + [인물] + [사전지식] 세 층으로 시스템 프롬프트를 만든다
(`Server/app.py` `_sess_build`). 공통 규칙은 서버가 갖고 있으므로 여기서는
[인물]과 [사전지식] 두 덩어리만 만든다.

형식과 근거는 `docs/페르소나_작성규격.md`. 특히 셋을 지킨다.

- **대화 예시를 반드시 넣는다.** 빼면 설교조가 네 배로 는다("중요한 건 그 경험에서
  뭘 배우느냐지"). 예시가 하는 일은 말투 유지가 아니라 어투 유지다.
- **종결어미를 직접 나열한다.** "반말로 하세요"만으로는 부족하고, 나열하면 그것만으로
  존댓말이 새지 않는다(80턴 0건).
- **사전지식 매 줄에서 누가 누구인지 밝힌다.** "친구 이름은 준호. 사용자는 준호야라고
  부른다"를 모델이 "사용자를 준호야라고 불러라"로 읽어, AI 가 사용자를 준호라고 불렀다.
"""
from __future__ import annotations

# ── 관계 → 말투 ──────────────────────────────────────────────────
# 관계 한 낱말이 말투 전체를 정한다. 설문은 자유 입력이라 낱말로 갈래를 찾는다.
KINDS = [
    # 목록에 없으면 또래로 떨어진다. 장모님이 "야, 그래서?" 하고 반말 추임새를 쓰게
    # 되므로 웃어른 쪽은 넉넉히 적는다. 빠진 것이 보이면 여기 낱말만 더하면 된다.
    ("어른", ["엄마", "어머니", "아빠", "아버지", "할머니", "할아버지", "외할", "친할",
              "고모", "이모", "삼촌", "숙모", "큰아버지", "작은아버지", "선생",
              "장모", "장인", "시어", "시아", "은사", "사부", "스승",
              "고모부", "이모부", "당숙", "외숙", "백부", "숙부"]),
    ("손위", ["형", "누나", "언니", "오빠", "선배"]),
    ("손아래", ["동생", "아들", "딸", "손자", "손녀", "조카", "후배", "막내", "제자"]),
    ("또래", ["친구", "동기", "동료", "짝", "남편", "아내", "배우자", "여자친구", "남자친구"]),
]

STYLE = {
    # 종결어미와 추임새는 어투를 결정한다. 예시와 함께 쓰여야 효과가 난다.
    # ask·comfort 는 [말투]에 그대로 실리는 문장이라 갈래마다 달라야 한다.
    # 전에는 또래 말투("그래서 넌 어떻게 하고 싶은데?")가 네 갈래에 모두 박혀 있어서,
    # 할머니 인물이 "-단다"로 끝내라는 규칙과 또래 예문을 같이 받고 있었다.
    "어른":   dict(endings='"-단다", "-구나", "-니", "-렴", "-지", "-야"',
                  fillers='"아이고", "그래서?", "저런"',
                  ask="그래서 넌 어쩔 셈이니?", comfort="많이 힘들었겠구나.",
                  self_ref=None),          # 관계어를 자기 지칭으로 쓴다(엄마·아빠…)
    "손위":   dict(endings='"-어", "-야", "-지", "-냐", "-라", "-네"',
                  fillers='"야", "그래서?", "아 진짜?"',
                  ask="그래서 넌 어떻게 하고 싶은데?", comfort="많이 힘들었겠다.",
                  self_ref="나"),
    "손아래": dict(endings='"-어", "-야", "-지", "-네", "-잖아"',
                  fillers='"어", "진짜?", "그래서?"',
                  ask="그래서 어떻게 하고 싶은데?", comfort="많이 힘들었겠다.",
                  self_ref="나"),
    "또래":   dict(endings='"-어", "-야", "-지", "-냐", "-자", "-네"',
                  fillers='"야", "그래서?", "아 진짜?"',
                  ask="그래서 넌 어떻게 하고 싶은데?", comfort="많이 힘들었겠다.",
                  self_ref="나"),
}

# 대화 예시 5쌍. 인사 / 힘든 얘기 / 고민 / 일상 / 감정 을 하나씩 덮는다.
# {u} 는 사용자를 부르는 말, {q} 는 말버릇.
EXAMPLES = {
    "어른": [("나 왔어", "왔니. 밥은 먹었고?"),
             ("요즘 좀 힘들어", "무슨 일 있었니? 얘기해 보렴."),
             ("일 그만둘까 생각 중이야", "그래서 넌 어쩔 셈이니?"),
             ("뭐 하고 있었어?", "그냥 앉아서 텔레비전 보고 있었지."),
             ("보고 싶었어", "나도 그랬단다. 자주 좀 오렴.")],
    "손위": [("오랜만이야", "오랜만이네. 얼굴은 좀 폈다?"),
             ("요즘 좀 힘들어", "무슨 일 있었어? 얘기해 봐."),
             ("일 그만둘까 고민 중이야", "그래서 넌 어떻게 하고 싶은데?"),
             ("뭐 하고 있었어?", "그냥 누워서 폰 보고 있었지. 왜?"),
             ("보고 싶었어", "나도. 얼굴 한번 보자.")],
    "손아래": [("오랜만이다", "오랜만이야. 잘 지냈어?"),
               ("요즘 좀 힘들어", "왜? 무슨 일인데?"),
               ("일 그만둘까 고민 중이야", "그래서 어떻게 하고 싶은데?"),
               ("뭐 하고 있었어?", "그냥 있었지 뭐. 왜?"),
               ("보고 싶었어", "나도 보고 싶었어.")],
    "또래": [("야 오랜만이다", "진짜 오랜만이네. 얼굴은 좀 폈다?"),
             ("요즘 좀 힘들어", "무슨 일 있었어? 얘기해 봐."),
             ("일 그만둘까 고민 중이야", "그래서 넌 어떻게 하고 싶은데?"),
             ("뭐 하고 있었어?", "그냥 누워서 폰 보고 있었지. 왜, 나와?"),
             ("보고 싶었어", "나도. 언제 한번 보자 진짜.")],
}


# 대화 톤 — 설문에서 고른 것이 [인물] 안의 한 줄이 된다.
TONES = {
    "warm_comfort": "상대의 감정을 먼저 받아 주고, 안심할 수 있게 말합니다. "
                    "재촉하지 않고 상대의 속도에 맞춥니다.",
    "casual_recreation": "특별할 것 없는 평소 대화처럼 말합니다. "
                         "생전에 하던 그대로, 사소한 것부터 묻습니다.",
    "free_dialogue": "화제는 상대가 정하게 두고, 꺼내는 이야기를 무엇이든 받아 줍니다.",
}


def kind_of(relation: str) -> str:
    """관계 낱말로 말투 갈래를 찾는다. 못 찾으면 또래로 둔다 — 가장 중립적이다."""
    r = (relation or "").strip()
    for kind, words in KINDS:
        if any(w in r for w in words):
            return kind
    return "또래"


def _clean(items) -> list[str]:
    return [s.strip() for s in (items or []) if s and s.strip()]


def _greet_with_quirk(greet: str, quirks: list) -> str:
    """첫 인사 예시 앞에 말버릇을 붙인다.

    예시는 그대로 복사돼 나온다 — 1턴 답변 127개 중 서로 다른 것이 16가지뿐이고
    둘이 68%를 먹었다(2026-08-26 실측). 그러니 **복사돼도 되는 문장**, 곧 그 사람이
    실제로 하던 말을 넣는 편이 낫다. 모두가 같은 첫 인사를 듣는 것보다 낫다.

    짧은 추임새만 쓴다. 설문은 "아이고~" 같은 것도 "밥은 먹었니" 같은 문장도 받는데,
    문장을 앞에 붙이면 인사와 겹쳐 어색해진다("밥은 먹었니, 왔니. 밥은 먹었고?").
    인사에 이미 나오는 낱말이 든 것도 거른다.
    """
    for q in quirks:
        q = q.strip().strip("~.!?, ")
        if not q or len(q) > 6:
            continue
        if any(q[i:i + 2] in greet for i in range(len(q) - 1)):
            continue
        return f"{q}, {greet}"
    return greet


def build_persona(d: dict) -> str:
    """설문 응답 → [인물]. 서버가 [인물] 항목으로 붙인다."""
    rel   = (d.get("relation") or "그리운 사람").strip()
    hon   = (d.get("honorific") or "").strip()          # 사용자가 그를 부르던 말
    calls = (d.get("calls_user") or "").strip()          # 그가 사용자를 부르던 말
    sex   = (d.get("sex") or "").strip()                 # 남자 / 여자 / (비움)
    traits = _clean(d.get("personality_traits"))
    quirks = _clean(d.get("speech_quirks"))

    kind = kind_of(rel)
    st = STYLE[kind]
    self_ref = st["self_ref"] or (hon or rel)            # 어른은 "엄마"처럼 관계어로 자칭한다

    L = []
    who = f"당신은 사용자의 {rel}입니다"
    if sex:
        who += f". 성별은 {sex}입니다"
    L.append(f"[캐릭터] {who}.")

    call = [f'자신은 "{self_ref}"라고 합니다.']
    if calls:
        call.append(f'사용자를 "{calls}"라고 부르는데, 말을 걸거나 화제를 돌릴 때만 '
                    f'부르고 대부분은 부르지 않고 바로 말합니다.')
    else:
        call.append("사용자 이름은 말을 걸 때만 부르고, 대부분은 부르지 않고 바로 말합니다.")
    L.append("[호칭] " + " ".join(call))

    if traits:
        L.append(f"[성격] {', '.join(traits)}. 진지한 얘기가 나오면 끝까지 듣습니다. "
                 f"걱정될 때는 바로 묻습니다.")
    else:
        L.append("[성격] 편안합니다. 진지한 얘기가 나오면 끝까지 듣습니다. "
                 "걱정될 때는 바로 묻습니다.")

    L.append(f"[말투] 반말로 말합니다. 문장은 {st['endings']} 로 끝납니다.")
    said = ", ".join(f'"{q}"' for q in quirks[:3]) if quirks else st["fillers"]
    L.append(f"- {said} 같은 말을 자주 씁니다.")
    L.append(f'- 고민을 들으면 먼저 되묻습니다. "{st["ask"]}"')
    L.append(f'- 힘들다는 말에는 그 말을 되받고 무슨 일인지 묻습니다. "{st["comfort"]}"')
    tone = TONES.get(d.get("tone_setting") or "casual_recreation")
    if tone:
        L.append(f"[대화 톤] {tone}")
    L.append("[예시 사용법] 아래는 말투의 본보기입니다. "
             "상황에 맞는 문장을 새로 만들어 말합니다.")
    L.append("")
    L.append("[대화 예시]")
    for i, (u, a) in enumerate(EXAMPLES[kind]):
        L.append(f"사용자: {u}")
        L.append(f"{rel}: {_greet_with_quirk(a, quirks) if i == 0 else a}")
    return "\n".join(L)


def build_knowledge(d: dict) -> str:
    """설문 응답 → [사전지식]. 한 줄에 한 사실, 매 줄에서 누가 누구인지 밝힌다."""
    rel   = (d.get("relation") or "그리운 사람").strip()
    hon   = (d.get("honorific") or "").strip()
    calls = (d.get("calls_user") or "").strip()
    name  = (d.get("user_name") or "").strip()

    L = []
    if hon:
        L.append(f'- 사용자는 당신({rel})을 "{hon}"라고 부른다.')
    if name:
        L.append(f"- 사용자의 이름은 {name}이다.")
    if calls:
        L.append(f'- 당신은 사용자를 "{calls}"라고 부른다.')
    for m in _clean(d.get("shared_memories")):
        L.append(f"- {m}")
    missed = (d.get("missed_moment") or "").strip()
    unsaid = (d.get("unsaid_words") or "").strip()
    wished = (d.get("wished_to_hear") or "").strip()
    if missed:
        L.append(f"- 사용자가 가장 그리워하는 순간은 이것이다: {missed}")
    # 못다 한 말과 듣고 싶은 말은 사용자의 것이다. 인물이 먼저 꺼내면 준비되지 않은
    # 사람을 밀어붙이는 것이 되고, 듣고 싶다던 말을 첫 마디로 뱉으면 값이 싸진다.
    if unsaid:
        L.append(f"- 사용자가 아직 전하지 못한 말이 있다: {unsaid}")
    if wished:
        L.append(f"- 사용자가 다시 듣고 싶어 하는 말이 있다: {wished}")
    if unsaid or wished:
        L.append("- 위 두 가지는 사용자가 스스로 꺼낼 때까지 먼저 말하지 않는다. "
                 "사용자가 꺼내면 그때 자연스럽게 받는다.")
    return "\n".join(L)


def build(d: dict) -> dict:
    return {"persona": build_persona(d), "knowledge": build_knowledge(d)}
