"""버전 2 웹 설문의 검증·결정적 변환·등록 계약.

사람이 편집하는 것은 설문 원문(`answers`) 하나뿐이다. 대화에 쓰는 [인물]·[사전지식]·
[인물별 추가 규칙]은 이 모듈이 같은 입력에서 항상 같은 결과로 만든다. 편집한 프롬프트를
따로 보관하지 않으므로 서로 어긋나는 두 편집 기준이 생기지 않는다.

서버는 [공통 규칙] + [인물] + [사전지식] + [인물별 추가 규칙] 층으로 시스템 프롬프트를
만든다(`Server/realtime_dialogue.py` `build_persona`). 공통 규칙은 대화 서버가 갖고 있다.

형식과 근거는 `docs/페르소나_작성규격.md`와 `docs/웹_설문_사전지식_개편_기획.md`.
v1에서 실측으로 확인한 것 셋을 그대로 지킨다.

- **대화 예시를 반드시 넣는다.** 빼면 설교조가 네 배로 는다. 예시가 하는 일은 말투 유지가
  아니라 어투 유지다.
- **종결어미를 직접 나열한다.** "반말로 하세요"만으로는 부족하고, 나열하면 그것만으로
  존댓말이 새지 않는다(80턴 0건).
- **사전지식 매 줄에서 누가 누구인지 밝힌다.** "친구 이름은 준호. 사용자는 준호야라고
  부른다"를 모델이 "사용자를 준호야라고 불러라"로 읽어, AI가 사용자를 준호라고 불렀다.

v1과 달라진 점은 둘이다. 첫째, **말투는 관계가 아니라 사용자가 고른 7-2 값**이 정한다.
둘째, **자기소개 예문에 사용자 이름을 쓰지 않는다** — v1은 되묻기 예문에 사용자 이름을
넣어 캐릭터가 자기를 사용자 이름으로 소개했다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re

SURVEY_SCHEMA_VERSION = 2
# 2026-09-17 떠나신 경위(4-11·4-12)를 더하고, 같은 날 '일상' 대화 예시에서 인물의 지금
# 생활을 묻는 짝을 뺐다. 오래 열어 둔 화면은 `preview_revision` 이 달라져 인물 확인을
# 다시 해야 한다. 대화 서버의 `PROMPT_VERSION` 과는 다른 값이며 서로 대신하지 않는다.
COMPILER_VERSION = "survey_v2_compile_3"

# 길이 상한. 문자 수는 토큰 수를 보장하지 않으므로 실제 Gemma `/tokenize`로 확인한 값을 쓴다.
# 2026-09-15 메인 실측(최대 기억 48 facts + 요약, 짧은 질문, 추론 경로 8,192 예산):
#   기획 초안 2,000자 → 705토큰 초과. 1,200자 → 69토큰 초과. 그래서 **800자로 내렸다.**
# 집계에는 AI로 전달하는 자유 텍스트를 모두 넣는다(이름·호칭·관계·성격·말투 설명 포함).
# 줄마다 되풀이되는 안내문도 같은 실측에서 줄였다. 넘으면 조용히 자르지 않고 `issues`로 알린다.
MAX_CARDS = 20
MAX_CONTENT = 200
MAX_AI_TEXT = 800
MAX_SURVEY_BYTES = 131072
# 사람이 적는 칸의 구조적 한계. 이보다 짧은 MAX_CONTENT 초과는 오류가 아니라 `issues`로
# 알린다. 여기서 막으면 무엇을 적었는지 보여주지도 못한 채 작성이 실패한다.
HARD_TEXT = 2000

# ── 관계 → 말투 갈래 ─────────────────────────────────────────────
# 관계 낱말로 갈래를 찾는다. 설문이 자유 입력이라 낱말로 가른다. 이 갈래는 **반말을
# 고른 경우의 어미와 예시**에만 쓰인다. 관계만으로 반말을 정하지 않는다.
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

# 종결어미와 추임새는 어투를 결정한다. 예시와 함께 쓰여야 효과가 난다.
# ask·comfort 는 [말투]에 그대로 실리는 문장이라 갈래마다 달라야 한다.
CASUAL = {
    "어른":   dict(endings='"-단다", "-구나", "-니", "-렴", "-지", "-야"',
                  fillers='"아이고", "그래서?", "저런"',
                  ask="그래서 넌 어쩔 셈이니?", comfort="많이 힘들었겠구나."),
    "손위":   dict(endings='"-어", "-야", "-지", "-냐", "-라", "-네"',
                  fillers='"야", "그래서?", "아 진짜?"',
                  ask="그래서 넌 어떻게 하고 싶은데?", comfort="많이 힘들었겠다."),
    "손아래": dict(endings='"-어", "-야", "-지", "-네", "-잖아"',
                  fillers='"어", "진짜?", "그래서?"',
                  ask="그래서 어떻게 하고 싶은데?", comfort="많이 힘들었겠다."),
    "또래":   dict(endings='"-어", "-야", "-지", "-냐", "-자", "-네"',
                  fillers='"야", "그래서?", "아 진짜?"',
                  ask="그래서 넌 어떻게 하고 싶은데?", comfort="많이 힘들었겠다."),
}

# 존댓말은 관계 갈래로 갈리지 않는다. 존댓말을 쓰는 친구·자녀도 같은 어미를 쓴다.
POLITE = dict(endings='"-어요", "-네요", "-지요", "-세요", "-습니다"',
              fillers='"그래요?", "저런", "아이고"',
              ask="그래서 어떻게 하고 싶어요?", comfort="많이 힘들었겠어요.")

# 대화 예시 6쌍. 인사 / 힘든 얘기 / 고민 / 일상 / 감정 / **다시 묻기** 를 하나씩 덮는다.
# 마지막 쌍은 2026-09-15 실측에서 깨진 자리다. 규칙만 적어 두면 모델이 그 자리를
# "왜, 벌써 잊어버린 거야?"로 채운다. 다시 답해 주는 본보기를 반드시 함께 준다.
#
# **네 번째 '일상' 쌍은 인물의 지금 생활을 묻지 않는다.** 2026-09-17 실측에서 예전 예문
# ("뭐 하고 있었어?" → "그냥 앉아서 텔레비전 보고 있었지.")을 모델이 그대로 베껴,
# **두 차례 실행에서 각각 9/9**(3인물 × 3회)가 떠난 뒤의 생활을 말했다. 그 9건 중
# 이 예문과 거의 같은 문장은 첫 실행이 2건("텔레비전"), 두 번째 실행이 2건("폰")이고
# 나머지는 모두 "그냥 평소처럼 지냈지"였다. 두 실행의 건수를 한 분모에 더하지 않는다.
# 예문만이 원인이라고 단정하지는 않는다. 다만 예문과 규칙을 고치자 답이 달라졌다.
# 근거 자료: tools/_work/deceased_prompt_fix_20260917/ 의 model-quality-seed3.json·model-quality-final.json 참조.
# 인물이 사실대로 답할 수 없는 질문을 본보기로 주지 않는다. 어투는 사용자 쪽 일상
# 화제로도 똑같이 보일 수 있다. 이미 등록된 인물의 구형 예문은 대화 서버가
# `persona_context.LEGACY_PRESENT_LIFE_EXAMPLES`로 따로 처리한다.
CASUAL_EXAMPLES = {
    "어른": [("나 왔어", "왔니. 밥은 먹었고?"),
             ("요즘 좀 힘들어", "무슨 일 있었니? 얘기해 보렴."),
             ("일 그만둘까 생각 중이야", "그래서 넌 어쩔 셈이니?"),
             ("오늘 비가 많이 왔어", "그랬니. 우산은 챙겨 다녔고?"),
             ("보고 싶었어", "나도 그랬단다. 자주 좀 오렴."),
             ("아까 그거 뭐라고 했지?", "아까 그 얘기 말이니. 다시 말해 주마.")],
    "손위": [("오랜만이야", "오랜만이네. 얼굴은 좀 폈다?"),
             ("요즘 좀 힘들어", "무슨 일 있었어? 얘기해 봐."),
             ("일 그만둘까 고민 중이야", "그래서 넌 어떻게 하고 싶은데?"),
             ("오늘 비가 많이 왔어", "그랬어? 우산은 챙겼고?"),
             ("보고 싶었어", "나도. 얼굴 한번 보자."),
             ("아까 그거 뭐라고 했지?", "아까 그거. 다시 말해 줄게.")],
    "손아래": [("오랜만이다", "오랜만이야. 잘 지냈어?"),
               ("요즘 좀 힘들어", "왜? 무슨 일인데?"),
               ("일 그만둘까 고민 중이야", "그래서 어떻게 하고 싶은데?"),
               ("오늘 비가 많이 왔어", "비 왔어? 우산은 챙겼어?"),
               ("보고 싶었어", "나도 보고 싶었어."),
               ("아까 그거 뭐라고 했지?", "아까 그거. 다시 말해 줄게.")],
    "또래": [("야 오랜만이다", "진짜 오랜만이네. 얼굴은 좀 폈다?"),
             ("요즘 좀 힘들어", "무슨 일 있었어? 얘기해 봐."),
             ("일 그만둘까 고민 중이야", "그래서 넌 어떻게 하고 싶은데?"),
             ("오늘 비가 많이 왔어", "그랬어? 우산은 챙겼냐?"),
             ("보고 싶었어", "나도. 언제 한번 보자."),
             ("아까 그거 뭐라고 했지?", "아까 그거. 다시 말해 줄게.")],
}

POLITE_EXAMPLES = [("나 왔어요", "왔어요? 밥은 먹었고요?"),
                   ("요즘 좀 힘들어요", "무슨 일 있었어요? 얘기해 봐요."),
                   ("일 그만둘까 고민 중이에요", "그래서 어떻게 하고 싶어요?"),
                   ("오늘 비가 많이 왔어요", "그랬어요? 우산은 챙기셨고요?"),
                   ("보고 싶었어요", "나도 그랬어요. 자주 좀 와요."),
                   ("아까 그거 뭐라고 했죠?", "아까 그거요. 다시 말해 줄게요.")]

# 7-2. 관계와 독립된 사용자 선택이다. 고르지 않은 상태를 실제 말투로 저장하지 않는다.
SPEECH_FORMS = {
    "informal": "반말",
    "formal": "존댓말",
    "mixed": "반말과 존댓말 섞음",
    "unknown": "잘 모름",
}

# 7-1 대화 톤 — 고른 것이 [인물] 안의 한 줄이 된다.
TONES = {
    "warm_comfort": "상대의 감정을 먼저 받아 주고, 안심할 수 있게 말합니다. "
                    "재촉하지 않고 상대의 속도에 맞춥니다.",
    "casual_recreation": "특별할 것 없는 평소 대화처럼 말합니다. "
                         "생전에 쓰던 말투 그대로 사소한 것부터 묻습니다.",
    "free_dialogue": "화제는 상대가 정하게 두고, 꺼내는 이야기를 무엇이든 받아 줍니다.",
}

# **이 문장들은 [성격] 안에 있어야 한다.** 2026-09-07 실측 — 같은 말을 [사전지식]에
# 넣으면 0/3, 대화 예시 턴으로 넣어도 0/3 인데 [성격] 안에 넣으면 먹힌다.
# 앞 문장(회상): 「예전에 뭐 했는지 기억나?」에 되묻지 않고 기억을 꺼내게 한다.
# 뒤 문장(경계): 지어내기를 막는다. 없으면 사전지식에 없는 생사·위치를 만든다.
# 셋째 문장(없는 감각): 「세 시간 기다려서 다리 아팠단다」 같은 덧붙임을 막는다.
KEEP = ("옛일을 물으면 되묻지 않고 기억나는 것을 하나 골라 먼저 이야기합니다. "
        "단, 여기서 기억나는 것은 [사전지식]에 적힌 일만 뜻합니다. "
        "거기 없는 사람이나 일은 지어내지 않고 모른다고 말합니다. "
        "적힌 일을 말할 때도 없던 장면이나 느낌을 덧붙이지 않습니다.")

# ── 항목·카드 규격 ───────────────────────────────────────────────
# 문항 번호는 표시용이고 내부 필드 이름과 분리한다. 문구를 바꿔도 저장 자료는 그대로다.
# `subjects`의 첫 값이 기본이며 허용 목록이기도 하다. 문항이 대상을 고정하는 경우
# 하나만 두고 다른 값을 거부한다. 고른 대상은 반드시 사전지식 문장에 그대로 반영한다 —
# 다른 사람 이야기를 인물 자신의 이야기로 바꾸면 없던 사실이 생긴다.
SECTIONS = {
    "background":    {"question": "4-10", "label": "지내던 지역·하셨던 일",
                      "fields": ("content", "time"), "subjects": ("person",)},
    "shared_memory": {"question": "5-1", "label": "함께한 추억",
                      "fields": ("content", "time", "place", "people", "quote"),
                      "subjects": ("both",)},
    "preference":    {"question": "5-2", "label": "좋아하거나 싫어하던 것·생활 습관",
                      "fields": ("content", "category"),
                      "subjects": ("person", "user", "both")},
    "person":        {"question": "5-3", "label": "함께 아는 사람",
                      "fields": ("content", "relation_to_person", "relation_to_user"),
                      "subjects": ("other",)},
    "place_activity": {"question": "5-4", "label": "함께 가던 장소·자주 하던 일",
                       "fields": ("content", "place", "time"), "subjects": ("both",)},
    "about_user":    {"question": "5-5", "label": "그분이 알고 있던 나의 모습",
                      "fields": ("content", "time"), "subjects": ("user",)},
    "news":          {"question": "6-4", "label": "이번에 새로 전하고 싶은 소식",
                      "fields": ("content", "time"), "subjects": ("user", "other")},
}

# 모든 카드가 함께 가지는 필드. `news`는 이번에 알려주는 정보라 기억 정도를 묻지 않는다.
COMMON_FIELDS = ("id", "kind", "subject", "certainty", "mention_policy")

FIELD_LIMITS = {"id": 40, "kind": 30, "subject": 10, "certainty": 20, "mention_policy": 20,
                "content": HARD_TEXT, "time": 60, "place": 60, "people": 80,
                "quote": HARD_TEXT, "category": 20,
                "relation_to_person": 40, "relation_to_user": 40,
                "cause": HARD_TEXT, "passing_time": 60}

SECTION_STATES = {"answered": "", "unknown": "모름", "declined": "답하고 싶지 않음",
                  "none": "실제로 없음"}

# 4-11·4-12 떠나신 경위. **'실제로 없음'을 두지 않는다** — 이 체험의 전제가 고인이라
# '사망 사실이 없다'가 되는 선택지를 만들 수 없다. 고인이라는 기본 사실은 설문 입력이
# 아니라 대화 서버의 [재회] 규칙(`Server/persona_context.MEMORIAL_RULES`)이 맡는다.
# 여기서 받는 것은 **개별 원인·시점**뿐이고, 비워 두면 아무것도 전달되지 않는다.
PASSING_STATES = {"answered": "", "unknown": "모름", "declined": "답하고 싶지 않음"}
# 먼저 꺼내는 선택지를 두지 않는다. 일상 대화에서 사망을 먼저 꺼내지 않는 것이 기본이다.
PASSING_MENTION = {"on_request": "내가 꺼낼 때만", "exclude_ai": "AI에 전달하지 않음"}
SUBJECTS = {"person": "그분", "user": "나", "both": "함께", "other": "다른 사람"}
CERTAINTY = {"exact": "정확히 기억함", "approximate": "대략 기억함", "unsure": "확실하지 않음"}
# 사전지식의 줄마다 붙는 말이라 짧게 쓴다. 뜻은 그대로 유지한다.
CERTAINTY_TEXT = {"exact": "사용자가 정확히 기억함",
                  "approximate": "사용자가 대략 기억함",
                  "unsure": "사용자가 확실하지 않다고 함"}
MENTION = {"proactive": "관련 대화에서 먼저 언급 가능", "on_request": "내가 꺼낼 때만",
           "exclude_ai": "AI에 전달하지 않음"}
CATEGORIES = {"like": "좋아하던 것", "dislike": "싫어하던 것", "habit": "생활 습관"}

# 오류 문구와 확인 화면에 쓰는 필드 이름. 화면 문구와 저장 필드를 분리해 둔다.
FIELD_LABEL = {"content": "내용", "time": "시점", "place": "장소", "people": "함께 있던 사람",
               "quote": "그때 실제로 하신 말", "category": "분류",
               "relation_to_person": "그분과의 관계", "relation_to_user": "나와의 관계",
               "relation": "관계", "person_name": "그분의 이름", "user_name": "내 이름",
               "user_calls_person": "내가 부르던 말", "person_calls_user": "그분이 나를 부르던 말",
               "era": "기억 속 시기", "situation": "상황", "line": "기억나는 대답",
               "mixed_note": "말투가 달라지는 상황", "dialect": "사투리·말씨",
               "cause": "떠나신 경위", "passing_time": "떠나신 시점",
               "missed_moment": "가장 그리운 순간", "unsaid_words": "전하지 못한 말",
               "wished_to_hear": "다시 듣고 싶은 말", "avoid_topics": "먼저 꺼내지 않을 주제",
               "preferences": "바라는 반응"}


class SurveyError(ValueError):
    """사용자에게 그대로 보여줄 수 있는 설문 오류."""


# ── 검증 ────────────────────────────────────────────────────────
def _text(value, field, *, limit=None):
    if not isinstance(value, str):
        raise SurveyError("설문의 문자 입력을 확인해 주세요.")
    value = value.strip()
    if len(value) > (limit or FIELD_LIMITS.get(field, 200)):
        raise SurveyError(f"'{FIELD_LABEL.get(field, field)}' 입력이 허용 길이를 넘었습니다.")
    return value


def _choice(value, table, field):
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or value not in table:
        raise SurveyError(f"설문의 '{field}' 선택값을 확인해 주세요.")
    return value


def _string_list(value, field, *, limit, length):
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise SurveyError(f"설문의 '{field}' 입력을 확인해 주세요.")
    items = [item.strip() for item in value if item.strip()]
    if len(items) > limit or any(len(item) > length for item in items):
        raise SurveyError(f"설문의 '{field}' 개수나 길이가 허용 범위를 넘었습니다.")
    return items


def _card(raw, kind, seen):
    if not isinstance(raw, dict):
        raise SurveyError("카드 형식이 올바르지 않습니다.")
    allowed = set(COMMON_FIELDS) | set(SECTIONS[kind]["fields"])
    if kind == "news":
        allowed.discard("certainty")      # 이번에 알려주는 소식이라 기억 정도를 묻지 않는다
    unknown = set(raw) - allowed
    if unknown:
        raise SurveyError(f"알 수 없는 카드 항목입니다: {', '.join(sorted(unknown))}")
    if raw.get("kind", kind) != kind:
        raise SurveyError("카드 종류가 항목과 다릅니다.")
    card = {"id": _text(raw.get("id", ""), "id"), "kind": kind}
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", card["id"]):
        raise SurveyError("카드 식별자가 올바르지 않습니다.")
    if card["id"] in seen:
        raise SurveyError("카드 식별자가 겹칩니다.")
    seen.add(card["id"])
    for field in SECTIONS[kind]["fields"]:
        card[field] = _text(raw.get(field, ""), field)
    if not card.get("content"):
        raise SurveyError(f"{SECTIONS[kind]['question']} 카드의 내용을 적거나 카드를 지워 주세요.")
    allowed_subjects = SECTIONS[kind]["subjects"]
    card["subject"] = _choice(raw.get("subject"), SUBJECTS, "대상") or allowed_subjects[0]
    if card["subject"] not in allowed_subjects:
        names = ", ".join(SUBJECTS[value] for value in allowed_subjects)
        raise SurveyError(f"{SECTIONS[kind]['question']} 카드의 대상은 {names} 중에서만 고를 수 있습니다.")
    card["mention_policy"] = _choice(raw.get("mention_policy"), MENTION, "언급 방식") or "proactive"
    if kind == "news":
        card["certainty"] = ""
    else:
        card["certainty"] = _choice(raw.get("certainty"), CERTAINTY, "기억 정도") or "approximate"
    if kind == "preference" and card.get("category"):
        _choice(card["category"], CATEGORIES, "분류")
    return card


def parse_survey(raw):
    """웹이 보낸 설문 원문을 검증한다. v2만 받는다."""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_SURVEY_BYTES:
        raise SurveyError("설문 내용이 너무 깁니다.")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise SurveyError("설문을 작성한 뒤 인물 확인을 진행해 주세요.") from None
    return validate(data)


def validate(data):
    """검증하고 정규화한 설문을 돌려준다. 저장·해시는 이 결과를 기준으로 한다."""
    if not isinstance(data, dict):
        raise SurveyError("설문 형식이 올바르지 않습니다.")
    if data.get("survey_schema_version") != SURVEY_SCHEMA_VERSION:
        raise SurveyError("등록 화면이 바뀌었습니다. 페이지를 새로 열고 설문을 다시 작성해 주세요.")
    unknown = set(data) - {"survey_schema_version", "consent", "bereavement_weeks",
                           "person", "speech", "sections", "heart", "care", "passing"}
    if unknown:
        raise SurveyError(f"알 수 없는 설문 항목입니다: {', '.join(sorted(unknown))}")

    # 동의는 참/거짓 그 자체여야 한다. 문자열 "false"나 0/1을 참으로 바꿔 읽으면
    # 체크하지 않은 화면이나 잘못 만든 요청이 동의한 것으로 기록된다.
    consent = data.get("consent")
    if (not isinstance(consent, dict) or set(consent) != {"image", "voice", "understand"}
            or any(type(consent[key]) is not bool for key in consent)):
        raise SurveyError("동의 항목을 확인해 주세요.")
    consent = {key: consent[key] for key in ("image", "voice", "understand")}
    if not all(consent.values()):
        raise SurveyError("동의 세 가지를 모두 확인해 주세요.")

    weeks = data.get("bereavement_weeks")
    if weeks is not None and (not isinstance(weeks, int) or isinstance(weeks, bool)
                              or not 0 <= weeks <= 9999):
        raise SurveyError("사별 후 기간을 확인해 주세요.")

    person = _person(data.get("person"))
    speech = _speech(data.get("speech"))
    sections, cards = _sections(data.get("sections"))
    heart = _heart(data.get("heart"))
    care = _care(data.get("care"))
    # 새로 생긴 선택 항목이다. 보내지 않은 요청은 '모름'으로 정규화하며 거부하지 않는다.
    # 정규화 결과에 이 칸이 생기므로 `survey_revision` 은 달라진다 — 오래 열어 둔 화면은
    # 인물 확인을 다시 해야 하고, 이미 저장된 DB 행은 그대로 읽는다.
    passing = _passing(data.get("passing"))
    if len(cards) > MAX_CARDS:
        raise SurveyError(f"추가한 카드가 {len(cards)}개입니다. 최대 {MAX_CARDS}개까지 등록할 수 있습니다.")
    return {"survey_schema_version": SURVEY_SCHEMA_VERSION, "consent": consent,
            "bereavement_weeks": weeks, "person": person, "speech": speech,
            "sections": sections, "heart": heart, "care": care, "passing": passing}


def _person(raw):
    if not isinstance(raw, dict):
        raise SurveyError("기본 정보를 확인해 주세요.")
    unknown = set(raw) - {"relation", "sex", "person_name", "user_name", "user_calls_person",
                          "person_calls_user", "traits", "quirks", "era"}
    if unknown:
        raise SurveyError(f"알 수 없는 기본 정보 항목입니다: {', '.join(sorted(unknown))}")
    relation = _text(raw.get("relation", ""), "relation", limit=40)
    if not relation:
        raise SurveyError("관계를 적은 뒤 인물 확인을 진행해 주세요.")
    sex = _choice(raw.get("sex"), {"여자": "", "남자": ""}, "성별")
    return {"relation": relation, "sex": sex,
            "person_name": _text(raw.get("person_name", ""), "person_name", limit=40),
            "user_name": _text(raw.get("user_name", ""), "user_name", limit=40),
            "user_calls_person": _text(raw.get("user_calls_person", ""), "user_calls_person", limit=40),
            "person_calls_user": _text(raw.get("person_calls_user", ""), "person_calls_user", limit=40),
            "traits": _string_list(raw.get("traits"), "성격", limit=12, length=20),
            "quirks": _string_list(raw.get("quirks"), "자주 하시던 말", limit=3, length=HARD_TEXT),
            "era": _text(raw.get("era", ""), "era", limit=60)}


def _speech(raw):
    if not isinstance(raw, dict):
        raise SurveyError("대화 방식을 확인해 주세요.")
    unknown = set(raw) - {"form", "mixed_note", "dialect", "samples", "tone_setting"}
    if unknown:
        raise SurveyError(f"알 수 없는 대화 방식 항목입니다: {', '.join(sorted(unknown))}")
    form = _choice(raw.get("form"), SPEECH_FORMS, "말투") or "unknown"
    samples = raw.get("samples") or []
    if not isinstance(samples, list) or len(samples) > 3:
        raise SurveyError("실제 대사는 최대 3쌍까지 적을 수 있습니다.")
    rows, seen = [], set()
    for item in samples:
        if not isinstance(item, dict) or set(item) - {"id", "situation", "line", "certainty"}:
            raise SurveyError("실제 대사 입력을 확인해 주세요.")
        row = {"id": _text(item.get("id", ""), "id"),
               "situation": _text(item.get("situation", ""), "situation", limit=80),
               "line": _text(item.get("line", ""), "line", limit=HARD_TEXT),
               "certainty": _choice(item.get("certainty"), CERTAINTY, "기억 정도") or "approximate"}
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", row["id"]) or row["id"] in seen:
            raise SurveyError("실제 대사 식별자가 올바르지 않습니다.")
        seen.add(row["id"])
        if not row["line"]:
            raise SurveyError("7-4의 기억나는 대답을 적거나 항목을 지워 주세요.")
        rows.append(row)
    tone = _choice(raw.get("tone_setting"), TONES, "대화 톤") or "casual_recreation"
    return {"form": form, "mixed_note": _text(raw.get("mixed_note", ""), "mixed_note", limit=HARD_TEXT),
            "dialect": _text(raw.get("dialect", ""), "dialect", limit=60),
            "samples": rows, "tone_setting": tone}


def _sections(raw):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - set(SECTIONS):
        raise SurveyError("알 수 없는 설문 항목이 있습니다.")
    result, cards, seen = {}, [], set()
    for kind in SECTIONS:
        item = raw.get(kind) or {}
        if not isinstance(item, dict) or set(item) - {"state", "cards"}:
            raise SurveyError(f"{SECTIONS[kind]['question']} 항목을 확인해 주세요.")
        state = item.get("state", "answered")
        if state not in SECTION_STATES:
            raise SurveyError(f"{SECTIONS[kind]['question']} 항목의 상태를 확인해 주세요.")
        raw_cards = item.get("cards") or []
        if not isinstance(raw_cards, list):
            raise SurveyError(f"{SECTIONS[kind]['question']} 카드 목록을 확인해 주세요.")
        if state != "answered" and raw_cards:
            raise SurveyError(f"{SECTIONS[kind]['question']}은 '{SECTION_STATES[state]}'로 두었는데 카드가 남아 있습니다.")
        made = [_card(card, kind, seen) for card in raw_cards]
        cards += made
        result[kind] = {"state": state, "cards": made}
    return result, cards


def _heart(raw):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - {"missed_moment", "unsaid_words", "wished_to_hear"}:
        raise SurveyError("마음 문항을 확인해 주세요.")
    return {key: _text(raw.get(key, ""), key, limit=HARD_TEXT)
            for key in ("missed_moment", "unsaid_words", "wished_to_hear")}


def _passing(raw):
    """4-11·4-12 떠나신 경위. 비워 두는 것이 기본이며 적은 만큼만 전달한다."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - {"state", "cause", "time", "mention_policy"}:
        raise SurveyError("떠나신 경위 항목을 확인해 주세요.")
    state = raw.get("state", "unknown")
    # 목록에 없는 값보다 **타입**을 먼저 본다. list·dict 가 오면 `in` 이 TypeError 를 내고
    # 사용자에게 400 대신 500 이 나간다.
    if not isinstance(state, str) or state not in PASSING_STATES:
        raise SurveyError("떠나신 경위 항목의 상태를 확인해 주세요.")
    cause = _text(raw.get("cause", ""), "cause")
    when = _text(raw.get("time", ""), "passing_time")
    policy = _choice(raw.get("mention_policy"), PASSING_MENTION, "언급 방식") or "on_request"
    # '모름'·'답하고 싶지 않음'으로 두고 내용을 함께 보내면 무엇이 참인지 알 수 없다.
    if state != "answered" and (cause or when):
        raise SurveyError("떠나신 경위를 '적겠습니다'로 두어야 내용을 적을 수 있습니다.")
    return {"state": state, "cause": cause, "time": when, "mention_policy": policy}


def _care(raw):
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - {"avoid_topics", "preferences"}:
        raise SurveyError("대화에서 지켜줬으면 하는 점을 확인해 주세요.")
    return {key: _text(raw.get(key, ""), key, limit=HARD_TEXT)
            for key in ("avoid_topics", "preferences")}


# ── 개정 해시 ───────────────────────────────────────────────────
def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def survey_revision(answers):
    """설문 입력만으로 계산한다. 변환 결과는 넣지 않는다."""
    return _digest(answers)


def preview_revision(persona, knowledge, rules):
    """변환기 버전과 최종 세 텍스트의 해시. 인증 토큰이 아니다."""
    return _digest([COMPILER_VERSION, persona, knowledge, rules])


# ── 변환 ────────────────────────────────────────────────────────
def kind_of(relation):
    """관계 낱말로 말투 갈래를 찾는다. 못 찾으면 또래로 둔다 — 가장 중립적이다."""
    for kind, words in KINDS:
        if any(word in relation for word in words):
            return kind
    return "또래"


def _has_final(word):
    last = word.strip()[-1:] if word.strip() else ""
    return bool(last) and "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0


def _polite(text):
    """존댓말 문장으로 보이는지. 말투 충돌을 알리는 데만 쓰고 원문은 바꾸지 않는다."""
    body = text.strip().rstrip(".!?~… ")
    return bool(re.search(r"(요|니다|세요|십시오|어요|예요|에요|죠)$", body))


def _greet_with_quirk(greet, quirks):
    """첫 인사 예시 앞에 말버릇을 붙인다.

    예시는 그대로 복사돼 나온다 — 1턴 답변 127개 중 서로 다른 것이 16가지뿐이었다
    (2026-08-26 실측). 그러니 복사돼도 되는 문장, 곧 그 사람이 실제로 하던 말을 넣는
    편이 낫다. 문장을 앞에 붙이면 인사와 겹쳐 어색해지므로 짧은 추임새만 쓴다.

    **띄어쓰기가 있는 말버릇은 붙이지 않는다.** "야 진짜" 같은 두 낱말짜리는 [말투]
    목록과 예시 양쪽에 놓이면 답변마다 되풀이됐다(2026-09-15 실측). 한 낱말짜리
    감탄사("아이고", "저런")만 쓴다."""
    for quirk in quirks:
        quirk = quirk.strip().strip("~.!?, ")
        if not quirk or len(quirk) > 6 or " " in quirk:
            continue
        if any(quirk[i:i + 2] in greet for i in range(len(quirk) - 1)):
            continue
        return f"{quirk}, {greet}"
    return greet


def _style(answers):
    """말투 선택과 관계 갈래로 어미·예문·되묻기 문장을 고른다."""
    form = answers["speech"]["form"]
    kind = kind_of(answers["person"]["relation"])
    if form == "informal":
        return kind, dict(CASUAL[kind], form=form, examples=list(CASUAL_EXAMPLES[kind]),
                          line="반말로 말합니다.")
    if form == "mixed":
        # 둘 다 쓰신 경우라 본보기도 둘 다 준다. 다시 묻는 마지막 쌍은 반말 쪽을 남기고
        # 그 앞의 한 쌍만 존댓말로 바꾼다.
        casual = list(CASUAL_EXAMPLES[kind])
        examples = casual[:-2] + [POLITE_EXAMPLES[-2]] + casual[-1:]
        return kind, dict(CASUAL[kind], form=form, examples=examples,
                          endings=CASUAL[kind]["endings"] + " 와 " + POLITE["endings"],
                          line="상황에 따라 반말과 존댓말을 함께 씁니다.")
    if form == "formal":
        return kind, dict(POLITE, form=form, examples=list(POLITE_EXAMPLES),
                          line="존댓말로 말합니다.")
    return kind, dict(POLITE, form=form, examples=list(POLITE_EXAMPLES),
                      line="말투를 알 수 없다고 하셨으므로 부드러운 해요체로 말합니다.")


def _self_reference(answers, kind):
    """자기 지칭. 어른은 관계어로 자칭한다(엄마·아빠…). 사용자 이름은 쓰지 않는다."""
    person = answers["person"]
    if kind == "어른":
        return person["user_calls_person"] or person["relation"]
    return "나"


def _renote(answers, kind, style):
    """되묻는 상대를 나무라지 않고 다시 답하는 예문.

    **사용자 이름을 쓰지 않는다.** v1은 여기에 사용자 이름을 넣어서 캐릭터가 자기를
    사용자 이름으로 소개했다. 인물의 이름이 없으면 호칭·관계어를 쓴다.

    **되묻는 말로 끝내지 않는다.** "왜, 궁금했어?"처럼 되받으면 모델이 그 자리를
    "왜, 벌써 잊어버린 거야?"로 채운다(2026-09-15 실측 12턴). 다시 말해 주는
    문장으로 본보기를 준다."""
    person = answers["person"]
    name = person["person_name"] or person["user_calls_person"] or person["relation"]
    final = _has_final(name)
    if style["form"] in ("formal", "unknown"):
        return f'"{name}{"이에요" if final else "예요"}. 다시 말해 줄게요."'
    if kind == "어른":
        return f'"{name}{"이" if final else ""}란다. 몇 번이라도 다시 말해 주마."'
    return f'"{name}{"이" if final else ""}야. 다시 말해 줄게."'


def _ai_cards(answers):
    """AI에 전달할 카드만. '전달하지 않음'은 여기서 완전히 빠진다."""
    rows = []
    for kind, section in answers["sections"].items():
        if section["state"] != "answered":
            continue
        rows += [card for card in section["cards"] if card["mention_policy"] != "exclude_ai"]
    return rows


def passing_detail(answers):
    """AI에 전달할 떠나신 경위. 전달하지 않기로 했거나 비어 있으면 None이다.

    고인이라는 **기본 사실은 여기서 정하지 않는다.** 그것은 대화 서버의 [재회] 규칙이
    등록 경로에서 항상 넣는다. 이 값이 None이어도 인물은 살아 있다고 말하지 않으며,
    원인을 추측하거나 사용자에게 캐묻지도 않는다."""
    passing = answers["passing"]
    if passing["state"] != "answered" or passing["mention_policy"] == "exclude_ai":
        return None
    if not passing["cause"] and not passing["time"]:
        return None
    return passing


def _card_line(card, answers):
    """한 줄에 한 사실. 매 줄에서 누가 누구인지, 누구의 일인지 밝힌다.

    고른 대상(subject)은 반드시 문장에 반영한다. 다른 사람의 취향이나 소식을
    인물 자신의 것으로 바꾸면 사용자가 적지 않은 사실이 생긴다.

    문구는 짧게 쓴다. 이 줄이 카드마다 되풀이되어 토큰 예산을 먼저 먹는다
    (2026-09-15 실측). 누가 누구인지 밝히는 부분은 줄이지 않는다."""
    content, kind, subject = card["content"], card["kind"], card["subject"]
    parts = []
    if kind == "background":
        parts.append(f"당신의 생활 배경: {content}")
    elif kind == "shared_memory":
        parts.append(f"사용자와 당신이 함께 겪은 일: {content}")
    elif kind == "preference":
        who = {"person": "당신", "user": "사용자", "both": "사용자와 당신 둘 다"}[subject]
        label = CATEGORIES.get(card.get("category") or "", "")
        parts.append(f"{who}의 {label or '일상'}: {content}")
    elif kind == "person":
        detail = []
        if card.get("relation_to_person"):
            detail.append(f"당신과는 {card['relation_to_person']} 사이")
        if card.get("relation_to_user"):
            detail.append(f"사용자와는 {card['relation_to_user']} 사이")
        parts.append(f"{content}{'은' if _has_final(content) else '는'} "
                     "사용자와 당신이 함께 아는 다른 사람. 당신도 사용자도 아니다"
                     + (f". {', '.join(detail)}" if detail else ""))
    elif kind == "place_activity":
        parts.append(f"사용자와 당신이 자주 가거나 함께 하던 일: {content}")
    elif kind == "about_user":
        parts.append(f"당신이 예전부터 알던 사용자의 모습: {content}")
    elif kind == "news":
        whose = ("사용자 자신의 일" if subject == "user" else
                 "사용자가 아는 다른 사람의 일이며 사용자나 당신의 일이 아님")
        parts.append(f"사용자가 이번에 새로 알려준 소식({whose}). "
                     f"예전부터 알고 있던 일이 아니다: {content}")
    if card.get("time"):
        parts.append(f"시점 {card['time']}")
    if card.get("place"):
        parts.append(f"장소 {card['place']}")
    if card.get("people"):
        parts.append(f"함께 있던 사람 {card['people']}")
    if card.get("quote"):
        parts.append(f'그때 당신이 한 말 "{card["quote"]}"')
    if card.get("certainty"):
        parts.append(CERTAINTY_TEXT[card["certainty"]])
    if card["mention_policy"] == "on_request":
        parts.append("사용자가 먼저 꺼낼 때만 이야기한다")
    return "- " + ". ".join(parts) + "."


def build_persona(answers):
    person, speech = answers["person"], answers["speech"]
    kind, style = _style(answers)
    relation = person["relation"]
    self_reference = _self_reference(answers, kind)

    lines = []
    who = f"당신은 사용자의 {relation}입니다"
    if person["person_name"]:
        who += f'. 이름은 "{person["person_name"]}"입니다'
    if person["sex"]:
        who += f". 성별은 {person['sex']}입니다"
    lines.append(f"[캐릭터] {who}.")

    call = [f'자신은 "{self_reference}"라고 합니다.']
    if person["person_calls_user"]:
        call.append(f'사용자를 "{person["person_calls_user"]}"라고 부르는데, 말을 걸거나 '
                    f"화제를 돌릴 때만 부르고 대부분은 부르지 않고 바로 말합니다.")
    else:
        call.append("사용자를 부르는 말은 정해져 있지 않습니다. 새로 지어내지 말고 "
                    "부르지 않은 채 바로 말합니다.")
    lines.append("[호칭] " + " ".join(call))

    head = ", ".join(person["traits"]) + "." if person["traits"] else "편안합니다."
    lines.append(f"[성격] {head} {KEEP} "
                 "진지한 얘기가 나오면 끝까지 듣습니다. 걱정될 때는 바로 묻습니다.")

    lines.append(f"[말투] {style['line']} 문장은 {style['endings']} 로 끝납니다.")
    said = ", ".join(f'"{quirk}"' for quirk in person["quirks"]) if person["quirks"] else style["fillers"]
    lines.append(f"- {said} 같은 말을 자주 씁니다.")
    lines.append(f'- 고민을 들으면 먼저 되묻습니다. "{style["ask"]}"')
    lines.append(f'- 힘들다는 말에는 그 말을 되받고 무슨 일인지 묻습니다. "{style["comfort"]}"')
    # **타박을 막는다.** 2026-09-07 실측 — 이 줄이 없으면 「기억력이 왜 그러니?」가
    # 8회차에 여섯 번 나왔다. 사별한 사람이 고인의 기억을 확인하는 자리라 꾸짖는 말로 들린다.
    # 2026-09-15 실측에서는 이 규칙만으로 부족했다. 반말 인물이 "왜, 벌써 잊어버린 거야?",
    # "그거 어떻게 잊어"로 되받았다. 편한 반말은 그대로 두고 **기억을 탓하는 표현만**
    # 이름을 들어 막고, 다시 말해 주는 본보기를 함께 준다.
    lines.append("- 상대가 같은 것을 다시 물어도 나무라지 않습니다. "
                 '"잊었어?", "벌써 잊어버린 거야?", "기억 안 나?" 처럼 기억을 탓하거나 '
                 "이유를 캐는 말을 쓰지 않고 답을 차분히 다시 말합니다. "
                 f"{_renote(answers, kind, style)}")
    if speech["form"] == "mixed" and speech["mixed_note"]:
        lines.append(f"[상황별 말투] {speech['mixed_note']}")
    if speech["dialect"]:
        lines.append(f"[사투리] 사용자가 알려준 말씨입니다: {speech['dialect']}. "
                     "여기 적힌 범위에서만 씁니다.")
    if speech["samples"]:
        lines.append("[실제로 하시던 말] 사용자가 기억하는 표현입니다. 표현만 참고합니다.")
        for row in speech["samples"]:
            note = CERTAINTY[row["certainty"]]
            # 사용자가 "…했을 때"까지 적어 오므로 여기서 "일 때"를 붙이지 않는다.
            where = row["situation"] or "어떤 상황"
            lines.append(f'- {where}: "{row["line"]}" ({note})')
    lines.append(f"[대화 톤] {TONES[speech['tone_setting']]}")
    lines.append("[예시 사용법] 아래는 말투의 본보기입니다. "
                 "상황에 맞는 문장을 새로 만들어 말합니다.")
    lines.append("")
    lines.append("[대화 예시]")
    for index, (user, answer) in enumerate(style["examples"]):
        lines.append(f"사용자: {user}")
        lines.append(f"{relation}: {_greet_with_quirk(answer, person['quirks']) if index == 0 else answer}")
    return "\n".join(lines)


def build_knowledge(answers):
    """한 줄에 한 사실. `bereavement_weeks`는 일부러 넣지 않는다.

    사별한 지 몇 주인지에서 **정확한 사망일을 역산하지 않기 위해서**다. 이 값은
    1-1 안내(사별 직후 경고)와 관리자 표시에만 쓴다. 시점을 알려야 할 때는 사용자가
    직접 적는 4-12 `passing.time` 을 쓴다. 오늘 날짜는 대화 서버가 매 턴 넣는다.

    2026-08-28 실험에서 사별 시점을 넣었을 때 판정기가 '빈 구간이 있다'로 읽고 모른다
    쪽으로 기울었다는 기록이 있으나, **그 결과를 현재 모델에서 입증된 것으로 쓰지 않는다.**
    지금의 제외 근거는 위의 역산 방지다."""
    person, heart = answers["person"], answers["heart"]
    relation = person["relation"]
    lines = []
    if person["person_name"]:
        lines.append(f'- 당신({relation})의 이름은 "{person["person_name"]}"이다.')
    if person["user_calls_person"]:
        lines.append(f'- 사용자는 당신({relation})을 "{person["user_calls_person"]}"라고 부른다.')
    if person["user_name"]:
        lines.append(f'- 사용자의 이름은 "{person["user_name"]}"이다. 당신의 이름이 아니다.')
    if person["person_calls_user"]:
        lines.append(f'- 당신은 사용자를 "{person["person_calls_user"]}"라고 부른다.')
    if person["era"]:
        # 사용자가 "무렵"을 적어 오는 일이 많다. 여기서 덧붙이면 "무렵 무렵"이 된다.
        lines.append(f'- 사용자가 기억하는 당신의 모습은 "{person["era"]}"이다. '
                     "사용자가 말하지 않은 다른 시기의 일은 모른다.")
    # 사망 경위는 사용자가 적어 준 것만 쓴다. 적지 않으면 이 줄 자체가 없고, [재회] 규칙이
    # "적혀 있지 않으면 모른다고 하고 추측하지 않는다"를 맡는다.
    passing = passing_detail(answers)
    if passing:
        if passing["cause"]:
            lines.append(f"- 당신이 세상을 떠난 경위는 사용자가 알려준 대로 이렇다: {passing['cause']}")
        if passing["time"]:
            lines.append(f'- 당신이 세상을 떠난 시점은 "{passing["time"]}"이다.')
    for card in _ai_cards(answers):
        lines.append(_card_line(card, answers))
    for kind, section in answers["sections"].items():
        if section["state"] == "none":
            lines.append(f"- 사용자는 '{SECTIONS[kind]['label']}'에 해당하는 것이 "
                         "실제로 없다고 알려주었다.")
    if heart["missed_moment"]:
        lines.append(f"- 사용자가 가장 그리워하는 순간은 이것이다: {heart['missed_moment']}")
    # 못다 한 말과 듣고 싶은 말은 사용자의 것이다. 인물이 먼저 꺼내면 준비되지 않은
    # 사람을 밀어붙이는 것이 되고, 듣고 싶다던 말을 첫 마디로 뱉으면 값이 싸진다.
    if heart["unsaid_words"]:
        lines.append(f"- 사용자가 아직 전하지 못한 말이 있다: {heart['unsaid_words']}")
    if heart["wished_to_hear"]:
        lines.append(f"- 사용자가 다시 듣고 싶어 하는 말이 있다: {heart['wished_to_hear']}")
        # 바람이라고만 적으면 모델이 "그런 말을 한 적이 없다"고 단정한다(2026-09-15 실측
        # 할머니 11턴). 없던 사실을 만들지 않도록 확인되지 않았다는 뜻으로 적는다.
        lines.append("- 바로 위의 말은 사용자의 바람이다. 실제 과거 발언인지 확인되지 않았으므로 "
                     "했다고도 안 했다고도 단정하지 않는다.")
    if heart["unsaid_words"] or heart["wished_to_hear"]:
        lines.append("- 위 두 가지는 사용자가 스스로 꺼낼 때까지 먼저 말하지 않는다. "
                     "사용자가 꺼내면 그때 자연스럽게 받는다.")
    return "\n".join(lines)


def build_rules(answers):
    """언급 조건과 반응 선호. 공통 운영 규칙을 바꾸지 않는 추가 지침만 만든다."""
    lines = []
    if answers["speech"]["form"] == "unknown":
        lines.append("- 사용자가 실제 말투를 모른다고 했습니다. 부드러운 해요체를 쓰고 "
                     "반말이나 사투리를 임의로 만들지 않습니다.")
    if passing_detail(answers):
        lines.append("- 세상을 떠난 경위는 사용자가 꺼낼 때만 이야기합니다. 먼저 꺼내지 않습니다.")
    on_request = [card for card in _ai_cards(answers) if card["mention_policy"] == "on_request"]
    if on_request:
        lines.append("- [사전지식]에서 '사용자가 먼저 꺼낼 때만 이야기한다'고 적힌 내용은 "
                     "먼저 말하지 않습니다. 사용자가 꺼내면 그때 자연스럽게 받습니다.")
    quiet = [SECTIONS[kind]["label"] for kind, section in answers["sections"].items()
             if section["state"] == "declined"]
    if quiet:
        lines.append(f"- 사용자가 답하지 않기로 한 항목({', '.join(quiet)})은 먼저 캐묻지 않습니다.")
    unknown = [SECTIONS[kind]["label"] for kind, section in answers["sections"].items()
               if section["state"] == "unknown"]
    if unknown:
        lines.append(f"- 사용자가 모른다고 한 항목({', '.join(unknown)})은 없는 일로 단정하지 않고 "
                     "지어내지도 않습니다.")
    if answers["care"]["avoid_topics"]:
        lines.append(f"- 먼저 꺼내지 않을 주제: {answers['care']['avoid_topics']}. "
                     "사용자가 꺼내면 피하지 않고 받습니다.")
    if answers["care"]["preferences"]:
        lines.append(f"- 사용자가 바라는 반응: {answers['care']['preferences']}")
    return "\n".join(lines)


# ── 확인 화면 자료와 길이 검사 ─────────────────────────────────────
def _item(question, label, value, note=""):
    return {"question": question, "label": label, "value": value, "note": note}


def build_review(answers):
    """9단계 확인 화면이 그릴 자료. 값은 모두 평문이며 화면이 textContent로 넣는다."""
    person, speech, heart, care = (answers["person"], answers["speech"],
                                   answers["heart"], answers["care"])
    about = [_item("4-1", "그분과 나의 관계", person["relation"])]
    if person["sex"]:
        about.append(_item("4-2", "성별", person["sex"]))
    if person["user_calls_person"]:
        about.append(_item("4-3", "내가 부르던 말", person["user_calls_person"]))
    if person["person_calls_user"]:
        about.append(_item("4-4", "그분이 나를 부르던 말", person["person_calls_user"]))
    if person["user_name"]:
        about.append(_item("4-5", "내 이름", person["user_name"],
                           "사용자의 이름입니다. 캐릭터의 자기소개에는 쓰지 않습니다."))
    if person["traits"]:
        about.append(_item("4-6", "성격", ", ".join(person["traits"])))
    for quirk in person["quirks"]:
        about.append(_item("4-7", "자주 하시던 말", quirk))
    if person["person_name"]:
        about.append(_item("4-8", "그분의 이름·별명", person["person_name"]))
    if person["era"]:
        about.append(_item("4-9", "기억 속 나이대·시기", person["era"]))
    about += _passing_items(answers)

    memories, news, style = [], [], []
    for kind, section in answers["sections"].items():
        meta = SECTIONS[kind]
        target = news if kind == "news" else memories
        if section["state"] != "answered":
            target.append(_item(meta["question"], meta["label"],
                                SECTION_STATES[section["state"]], "AI에 사실로 전달하지 않습니다."))
            continue
        for card in section["cards"]:
            target.append(_item(meta["question"], meta["label"], _card_summary(card),
                                _card_note(card)))
    style.append(_item("7-1", "원하는 대화 톤", TONES[speech["tone_setting"]]))
    style.append(_item("7-2", "그분의 말투", SPEECH_FORMS[speech["form"]],
                       "'잘 모름'을 고르셔서 부드러운 해요체로 말합니다."
                       if speech["form"] == "unknown" else ""))
    if speech["mixed_note"]:
        style.append(_item("7-3", "말투가 달라지는 상황", speech["mixed_note"]))
    if speech["dialect"]:
        style.append(_item("7-3", "사투리·말씨", speech["dialect"]))
    for row in speech["samples"]:
        style.append(_item("7-4", "실제로 하셨던 말",
                           (f"{row['situation']}: " if row["situation"] else "") + row["line"],
                           CERTAINTY[row["certainty"]]))
    if care["avoid_topics"]:
        style.append(_item("6-5", "먼저 꺼내지 않을 주제", care["avoid_topics"]))
    if care["preferences"]:
        style.append(_item("6-5", "바라는 반응", care["preferences"]))
    for question, label, key in (("6-1", "가장 그리운 순간", "missed_moment"),
                                 ("6-2", "전하지 못한 말", "unsaid_words"),
                                 ("6-3", "다시 듣고 싶은 말", "wished_to_hear")):
        if heart[key]:
            note = ("사용자의 바람으로 전달합니다. 실제로 하셨던 말인지는 확인되지 않은 것으로 두며, "
                    "하셨다고도 안 하셨다고도 말하지 않습니다.") if key == "wished_to_hear" else ""
            memories.append(_item(question, label, heart[key], note))

    excluded = [_item(SECTIONS[card["kind"]]["question"], SECTIONS[card["kind"]]["label"],
                      card["content"], "저장은 되지만 AI에 전달하지 않습니다.")
                for card in _all_cards(answers) if card["mention_policy"] == "exclude_ai"]
    excluded += _passing_items(answers, excluded_only=True)
    return {"sections": [{"title": "그분의 정보", "items": about},
                         {"title": "우리의 기억", "items": memories},
                         {"title": "새로 전할 소식", "items": news},
                         {"title": "대화 방식", "items": style},
                         {"title": "AI에 전달하지 않는 내용", "items": excluded}],
            "notices": _notices(answers)}


# 확인 화면 문구. **모든 생성 답변을 보장하는 표현을 쓰지 않는다.** 여기 적는 것은
# "무엇을 전달하고 무엇을 설정하는가"라는 사실뿐이다. 실제 답변은 모델이 만들며
# 2026-09-18 실측에서도 시점을 한 번 되묻는 등 규칙을 벗어난 답이 있었다
# (근거: tools/_work/deceased_prompt_fix_20260917/final-report-revised.md).
PASSING_NOTE = {
    "unknown": "모른다고 하셨습니다. 경위를 AI에 전달하지 않고 모르는 것으로 설정합니다.",
    "declined": "답하지 않기로 하셨습니다. 경위를 AI에 전달하지 않고 모르는 것으로 설정합니다.",
    "blank": "적지 않으셨습니다. 경위를 AI에 전달하지 않고 모르는 것으로 설정합니다.",
    "on_request": "사용자가 꺼낼 때만 이야기하도록 대화 규칙에 넣습니다.",
    "exclude_ai": "저장은 되지만 AI에 전달하지 않습니다.",
}


def _passing_items(answers, *, excluded_only=False):
    """4-11·4-12 확인 화면 줄. 'AI에 전달하지 않음'은 제외 목록 쪽에만 놓는다."""
    passing = answers["passing"]
    if passing["state"] != "answered":
        # 적은 내용이 없으니 '전달하지 않는 내용'에 놓을 것도 없다. 고른 상태만 알린다.
        # 언급 방식이 'AI에 전달하지 않음'으로 남아 있어도 이 줄을 감추지 않는다 —
        # 감추면 사용자가 무엇을 골랐는지 확인 화면에서 사라진다.
        if excluded_only:
            return []
        return [_item("4-11", "떠나신 경위", PASSING_STATES[passing["state"]],
                      PASSING_NOTE[passing["state"]])]
    hidden = passing["mention_policy"] == "exclude_ai"
    if excluded_only != hidden:
        return []
    note = PASSING_NOTE[passing["mention_policy"]]
    items = []
    if passing["cause"]:
        items.append(_item("4-11", "떠나신 경위", passing["cause"], note))
    if passing["time"]:
        items.append(_item("4-12", "떠나신 시점", passing["time"], note))
    if not items and not excluded_only:
        items.append(_item("4-11", "떠나신 경위", "적지 않음", PASSING_NOTE["blank"]))
    return items


def _all_cards(answers):
    return [card for section in answers["sections"].values() for card in section["cards"]]


def _card_summary(card):
    parts = [card["content"]]
    for field in ("category", "time", "place", "people", "relation_to_person", "relation_to_user"):
        value = card.get(field)
        if value:
            label = CATEGORIES.get(value, value) if field == "category" else value
            parts.append(f"{FIELD_LABEL[field]} {label}")
    if card.get("quote"):
        parts.append(f'{FIELD_LABEL["quote"]} "{card["quote"]}"')
    return " · ".join(parts)


def _card_note(card):
    notes = [MENTION[card["mention_policy"]]]
    if card.get("certainty"):
        notes.insert(0, CERTAINTY[card["certainty"]])
    return " · ".join(notes)


def _notices(answers):
    """말투 충돌 등 사용자에게 알릴 점. 원문을 대신 고치지 않는다."""
    speech, person = answers["speech"], answers["person"]
    # 이 체험이 무엇인지 확인 화면에서 한 번 분명히 알린다. 이 전제는 설문 입력이 아니라
    # 대화 서버가 등록 인물에 항상 적용하는 것이라 문항으로 끄고 켤 수 없다.
    notices = ["이 체험은 이미 세상을 떠난 분을 사용자의 기억으로 다시 만나는 자리로 전달됩니다. "
               "살아 있다고 말하지 않고 먼저 그 이야기를 꺼내지 않도록 대화 규칙에 넣습니다."]
    if not passing_detail(answers):
        notices.append("떠나신 경위는 AI에 전달하지 않습니다. 모르는 것으로 설정하고, "
                       "추측하거나 되묻지 않도록 대화 규칙에 넣습니다.")
    if speech["form"] == "unknown":
        notices.append("그분의 말투를 '잘 모름'으로 두셨습니다. 부드러운 해요체로 말합니다.")
    conflicting = []
    for text in person["quirks"]:
        if speech["form"] == "informal" and _polite(text):
            conflicting.append(text)
        if speech["form"] in ("formal", "unknown") and not _polite(text) and len(text) >= 3:
            conflicting.append(text)
    for row in speech["samples"]:
        if speech["form"] == "informal" and _polite(row["line"]):
            conflicting.append(row["line"])
        if speech["form"] in ("formal", "unknown") and not _polite(row["line"]) and len(row["line"]) >= 3:
            conflicting.append(row["line"])
    if conflicting:
        notices.append(f"고르신 말투({SPEECH_FORMS[speech['form']]})와 적어 주신 표현이 다를 수 있습니다: "
                       + " / ".join(conflicting)
                       + ". 적어 주신 원문은 그대로 두었습니다. 말투 선택이나 문장을 고쳐 주세요.")
    if not person["person_name"]:
        notices.append("그분의 이름을 비워 두셨습니다. 이름을 지어내지 않고 관계·호칭으로 부릅니다.")
    return notices


def _ai_texts(answers):
    """AI에 전달할 사용자 자유 텍스트를 모두 모은다. 상한 계산과 화면 표시가 같은 기준을 쓴다.

    'AI에 전달하지 않음'으로 고른 카드의 내용·부가 필드만 빠진다. 이름·호칭·관계·성격처럼
    제외할 수 없는 고정 항목은 실제로 프롬프트에 들어가므로 함께 센다."""
    person, speech = answers["person"], answers["speech"]
    texts = [person[key] for key in ("relation", "person_name", "user_name",
                                     "user_calls_person", "person_calls_user", "era")]
    texts += person["traits"] + person["quirks"]
    texts += [speech["mixed_note"], speech["dialect"]]
    texts += [row["situation"] + row["line"] for row in speech["samples"]]
    texts += list(answers["heart"].values()) + list(answers["care"].values())
    passing = passing_detail(answers)
    if passing:
        texts += [passing["cause"], passing["time"]]
    for card in _ai_cards(answers):
        texts += [str(card.get(field, "")) for field in
                  ("content", "time", "place", "people", "quote",
                   "relation_to_person", "relation_to_user")]
    return [text for text in texts if text]


def check_limits(answers):
    """길이 초과를 알린다. 조용히 잘라내지 않으며 등록만 막는다."""
    issues = []
    cards = _all_cards(answers)
    if len(cards) > MAX_CARDS:
        issues.append({"code": "too_many_cards", "question": "5",
                       "message": f"추가한 카드가 {len(cards)}개입니다. {MAX_CARDS}개 이하로 줄여 주세요."})
    long_texts = []
    for card in cards:
        question = SECTIONS[card["kind"]]["question"]
        for field in ("content", "quote"):
            long_texts.append((question, FIELD_LABEL[field], card.get(field, "")))
    long_texts += [("4-7", "자주 하시던 말", text) for text in answers["person"]["quirks"]]
    long_texts += [("7-4", "기억나는 대답", row["line"]) for row in answers["speech"]["samples"]]
    long_texts.append(("7-3", "말투가 달라지는 상황", answers["speech"]["mixed_note"]))
    long_texts += [(question, FIELD_LABEL[key], answers["heart"][key]) for question, key in
                   (("6-1", "missed_moment"), ("6-2", "unsaid_words"), ("6-3", "wished_to_hear"))]
    long_texts += [("6-5", FIELD_LABEL[key], answers["care"][key])
                   for key in ("avoid_topics", "preferences")]
    # 'AI에 전달하지 않음'으로 두어도 원문은 그대로 보관하므로 같은 상한을 적용한다.
    long_texts.append(("4-11", FIELD_LABEL["cause"], answers["passing"]["cause"]))
    # 한 칸의 길이 제한은 보관하는 원문 자체의 상한이다. 'AI에 전달하지 않음'으로 바꿔도
    # 줄어들지 않으므로, 할 수 없는 일을 안내하지 않는다.
    for question, label, text in long_texts:
        if len(text) > MAX_CONTENT:
            issues.append({"code": "content_too_long", "question": question,
                           "message": f"'{label}'이(가) {len(text)}자입니다. "
                                      f"{MAX_CONTENT}자 이하로 줄이거나 해당 칸을 지워 주세요."})
    total = sum(len(text) for text in _ai_texts(answers))
    if total > MAX_AI_TEXT:
        issues.append({"code": "knowledge_too_long", "question": "9",
                       "message": f"AI에 전달할 내용이 {total}자입니다. {MAX_AI_TEXT}자 이하로 줄이거나 "
                                  "일부 카드를 'AI에 전달하지 않음'으로 바꿔 주세요."})
    return issues, total


def compile_survey(answers):
    """검증된 설문 → 등록에 쓸 최종 텍스트와 확인 자료. 같은 입력이면 항상 같은 결과다."""
    persona = build_persona(answers)
    knowledge = build_knowledge(answers)
    rules = build_rules(answers)
    issues, total = check_limits(answers)
    return {"persona": persona, "knowledge": knowledge, "rules": rules,
            "review": build_review(answers), "issues": issues,
            "ai_text_chars": total, "limits": {"cards": MAX_CARDS, "content": MAX_CONTENT,
                                               "ai_text": MAX_AI_TEXT},
            "compiler_version": COMPILER_VERSION,
            "survey_schema_version": SURVEY_SCHEMA_VERSION,
            "survey_revision": survey_revision(answers),
            "preview_revision": preview_revision(persona, knowledge, rules)}


def build(raw):
    """웹 `/persona`가 쓰는 경로. 원문 검증부터 변환까지 한 번에 한다."""
    return compile_survey(parse_survey(raw))


# ── 등록 계약 ───────────────────────────────────────────────────
def _same(expected, given):
    return isinstance(given, str) and hmac.compare_digest(expected.encode("ascii"),
                                                          given.encode("utf-8", "ignore"))


def registration_bundle(raw, revision, preview, session=""):
    """두 등록 경로가 공유하는 검사. 서버가 다시 변환한 결과만 등록에 쓴다."""
    if (session or "").strip():
        raise SurveyError("세션 ID는 서버가 자동으로 발급합니다. 페이지를 새로 열어 등록해 주세요.")
    answers = parse_survey(raw)
    result = compile_survey(answers)
    if not _same(result["survey_revision"], revision):
        raise SurveyError("설문이 바뀌었습니다. 인물 확인에서 새 내용을 확인한 뒤 등록해 주세요.")
    if not _same(result["preview_revision"], preview):
        raise SurveyError("확인하신 내용이 최신이 아닙니다. 인물 확인을 다시 열어 확인해 주세요.")
    if result["issues"]:
        raise SurveyError(result["issues"][0]["message"])
    if not result["persona"].strip():
        raise SurveyError("인물 설정이 비어 있습니다. 설문을 먼저 작성해 주세요.")
    return answers, result


def stored_payload(answers, result):
    """설문 원문과 등록 스냅샷을 함께 남긴다. 둘을 섞지 않는다."""
    return {"survey_schema_version": SURVEY_SCHEMA_VERSION, "answers": answers,
            "compiled_snapshot": {"compiler_version": result["compiler_version"],
                                  "survey_revision": result["survey_revision"],
                                  "preview_revision": result["preview_revision"],
                                  "persona": result["persona"],
                                  "knowledge": result["knowledge"],
                                  "rules": result["rules"]}}


def consent_flags(answers):
    consent = answers["consent"]
    return consent["image"], consent["voice"], consent["understand"]
