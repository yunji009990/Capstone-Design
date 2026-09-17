"""말버릇 반복 억제. [말투] 줄만 정규화하고, 들려준 답변에 쓴 말은 다음 턴에 피한다.

수정 전 기준값(메인 측정, 합성 2역할 × 12턴 × 3회 = 72턴): 할머니 "아이고" 18/36턴·
연속 5쌍, 친구 "진짜" 11/36턴·연속 3쌍. 성공 수치가 아니라 비교 기준이며, 이 파일의
단위 검사는 모의 입력이라 실제 Gemma 반복률을 재지 않는다.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from persona_context import QUIRK_MAX_LENGTH, QUIRK_TAIL, quirks_of, uses_quirk
from realtime_audio import Observation
from realtime_dialogue import Dialogue, ResponseState, build_persona
from test_dialogue import detector


# Web/survey_v2.build_persona 의 출력 형식을 본뜬 합성 인물이다. 운영 인물이 아니다.
# [실제로 하시던 말] 의 두 줄은 자유 입력이 우연히 같은 모양이 된 경우다.
PERSONA = """[캐릭터] 당신은 사용자의 할머니입니다.
[호칭] 자신은 "할머니"라고 합니다. 사용자를 "우리 강아지"라고 부릅니다.
[말투] 반말로 말합니다. 문장은 "-단다", "-구나" 로 끝납니다.
- "아이고", "그려" 같은 말을 자주 씁니다.
- 고민을 들으면 먼저 되묻습니다. "그래서 넌 어쩔 셈이니?"
- 상대가 같은 것을 다시 물어도 나무라지 않습니다.
[실제로 하시던 말] 사용자가 기억하는 표현입니다. 표현만 참고합니다.
- 아침마다: "아이고" 같은 말을 자주 씁니다.
- "밥은 먹었니", "춥다" 같은 말을 자주 씁니다.
[예시 사용법] 아래는 말투의 본보기입니다.

[대화 예시]
사용자: 나 왔어
할머니: 아이고, 왔니. 밥은 먹었고?
사용자: 뭐 하고 있었어?
할머니: 그냥 앉아서 텔레비전 보고 있었지."""


async def emit(event):
    pass


def make(persona=PERSONA, **kwargs):
    system, examples = build_persona(persona)
    return Dialogue(system, examples, None, None, emit, detector=detector(), **kwargs)


def deliver(dialogue, heard, answer, *, spoken=None):
    """한 턴을 들려준 것까지 기록한다. spoken 은 재생이 확인된 글자 수다."""
    dialogue.turn_id += 1
    dialogue.append_history({"role": "user", "content": heard})
    state = ResponseState(f"r{dialogue.turn_id}", dialogue.turn_id)
    state.user_added, state.text = True, answer
    if spoken is not None:
        state.spoken_chars = spoken
    dialogue.record(state)


def system_of(dialogue, heard="오늘 뭐 했어?"):
    return dialogue.messages(Observation(heard))[0]["content"]


class QuirkFormatTests(unittest.TestCase):
    def test_only_the_speech_section_line_is_normalized(self):
        system, _ = build_persona(PERSONA)
        self.assertEqual(quirks_of(system), ["아이고", "그려"])
        # 같은 모양의 줄이 셋인데 바뀐 줄은 [말투] 영역의 하나뿐이다.
        self.assertEqual(PERSONA.count(QUIRK_TAIL) - system.count(QUIRK_TAIL), 1)
        self.assertNotIn('- "아이고", "그려" ' + QUIRK_TAIL, system)
        for kept in ('[호칭] 자신은 "할머니"라고 합니다',
                     '문장은 "-단다", "-구나" 로 끝납니다.',
                     '- 고민을 들으면 먼저 되묻습니다. "그래서 넌 어쩔 셈이니?"',
                     "- 상대가 같은 것을 다시 물어도 나무라지 않습니다.",
                     '- 아침마다: "아이고" 같은 말을 자주 씁니다.',
                     '- "밥은 먹었니", "춥다" 같은 말을 자주 씁니다.'):
            self.assertIn(kept, system)
        for quirk in ("아이고", "그려"):
            self.assertIn(quirk, system)      # 말버릇 자체는 사라지지 않는다

    def test_a_persona_without_a_speech_section_is_left_alone(self):
        persona = ('자유롭게 적은 인물 설명.\n'
                   '- "아이고", "그려" 같은 말을 자주 씁니다.')
        system, _ = build_persona(persona)
        self.assertIn(persona, system)
        self.assertEqual(quirks_of(system), [])

    def test_malformed_speech_lines_are_preserved(self):
        long_quirk = "길" * (QUIRK_MAX_LENGTH + 1)
        for line in ("- 아이고, 그려 같은 말을 자주 씁니다.",       # 따옴표 목록이 아니다
                     '- "아이고" 같은 말을 자주 쓰셨습니다.',        # 문구가 다르다
                     '  "아이고" 같은 말을 자주 씁니다.',            # 목록 항목이 아니다
                     '- "아이고", 밥은 먹었니 같은 말을 자주 씁니다.',
                     f'- "{long_quirk}" 같은 말을 자주 씁니다.'):   # 말버릇이 아니라 서술
            with self.subTest(line=line[:16]):
                persona = "[말투] 반말로 말합니다.\n" + line
                system, _ = build_persona(persona)
                self.assertIn(persona, system)
                self.assertEqual(quirks_of(system), [])

    def test_a_comma_inside_quotes_is_not_an_item_boundary(self):
        persona = ('[말투] 반말로 말합니다.\n'
                   '- "밥은 먹었니, 우리 강아지", "그려" 같은 말을 자주 씁니다.')
        system, _ = build_persona(persona)
        self.assertEqual(quirks_of(system), ["밥은 먹었니, 우리 강아지", "그려"])

    def test_examples_keep_their_text_and_only_gain_a_reading_note(self):
        plain = PERSONA.replace("할머니: 아이고, 왔니.", "할머니: 왔니.")
        with_quirk, examples = build_persona(PERSONA)
        without, plain_examples = build_persona(plain)
        self.assertIn("아이고, 왔니. 밥은 먹었고?", with_quirk)   # 예시 원문은 고치지 않는다
        self.assertEqual(len(examples), len(plain_examples))
        # 예시에 말버릇이 있을 때만 읽는 법이 덧붙어 그만큼 길어진다.
        self.assertGreater(len(with_quirk) - len(PERSONA), len(without) - len(plain))


class QuirkBoundaryTests(unittest.TestCase):
    def test_a_one_letter_quirk_counts_only_as_its_own_word(self):
        for text in ("야, 왔어?", "그래서 야 말이지", "야"):
            self.assertTrue(uses_quirk(text, "야"), text)
        for text in ("야구 봤단다", "이야기 좀 하자", "어제 야근했지"):
            self.assertFalse(uses_quirk(text, "야"), text)
        self.assertTrue(uses_quirk("어, 그랬구나", "어"))
        for text in ("어제 봤단다", "어머니가 오셨어"):
            self.assertFalse(uses_quirk(text, "어"), text)

    def test_longer_quirks_are_not_matched_inside_other_words(self):
        self.assertTrue(uses_quirk("진짜? 언제 그랬어", "진짜"))
        self.assertFalse(uses_quirk("진짜로 그랬단다", "진짜"))
        # 끝의 구두점은 비교용으로만 맞춘다. 원문 표기는 그대로 둔다.
        self.assertTrue(uses_quirk("밥은 먹었니? 얼른 앉으렴", "밥은 먹었니?"))
        self.assertTrue(uses_quirk("아이고, 우리 강아지", "아이고~"))


class QuirkRepetitionTests(unittest.IsolatedAsyncioTestCase):
    """ResponseState 는 asyncio.Event 를 만든다. 검사마다 자기 루프를 쓴다.

    동기 검사에서 asyncio.run 을 쓰면 그 루프가 닫힌 뒤 다음 검사의 ResponseState
    생성이 실패해, 실패가 검사 순서에 따라 옮겨 다녔다.
    """
    async def test_one_use_in_a_delivered_answer_blocks_the_next_turn(self):
        dialogue, base = make(), make()
        plain = system_of(base)
        self.assertEqual(system_of(dialogue), plain)
        deliver(dialogue, "나 왔어", "아이고, 어서 오렴.")
        note = system_of(dialogue)
        self.assertTrue(note.startswith(plain))
        added = note[len(plain):]
        self.assertIn("아이고", added)
        self.assertNotIn("그려", added)                 # 쓰지 않은 말은 막지 않는다
        # 회피 지시는 system 에만 붙는다. 기록과 사용자 메시지는 그대로다.
        messages = dialogue.messages(Observation("오늘 뭐 했어?"))
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(messages[-1], base.messages(Observation("오늘 뭐 했어?"))[-1])
        self.assertEqual(dialogue.history[-1], {"role": "assistant", "content": "아이고, 어서 오렴."})

    async def test_avoidance_lasts_the_recent_window_and_then_ends(self):
        dialogue, base = make(), make()
        plain = system_of(base)
        deliver(dialogue, "나 왔어", "아이고, 어서 오렴.")
        for answer in ("그건 참 좋구나.", "그래, 천천히 하렴."):
            deliver(dialogue, "응", answer)
            self.assertNotEqual(system_of(dialogue), plain)   # 아직 최근 3답변 안이다
        deliver(dialogue, "응", "밥부터 먹자꾸나.")
        self.assertEqual(system_of(dialogue), plain)

    async def test_a_standalone_one_letter_quirk_is_suppressed_but_other_words_are_not(self):
        persona = PERSONA.replace('- "아이고", "그려" 같은', '- "야", "어" 같은')
        dialogue, other, base = make(persona), make(persona), make(persona)
        self.assertEqual(dialogue.quirks, ["야", "어"])
        plain = system_of(base)
        deliver(dialogue, "나 왔어", "야, 왔어? 어, 앉으렴.")
        added = system_of(dialogue)[len(plain):]
        self.assertIn("야", added)
        self.assertIn("어", added)
        deliver(other, "야구 봤어?", "어제 야구 보다가 이야기를 나눴단다.")
        self.assertEqual(system_of(other), plain)

    async def test_all_registered_quirks_are_avoided_at_once(self):
        # 설문은 말버릇을 3개까지 받는다. 세 개를 한 답변에 다 써도 남김없이 피한다.
        quirks = ["아이고", "그려", "저런"]
        persona = PERSONA.replace('- "아이고", "그려" 같은',
                                  "- " + ", ".join(f'"{q}"' for q in quirks) + " 같은")
        dialogue, base = make(persona), make(persona)
        self.assertEqual(dialogue.quirks, quirks)
        deliver(dialogue, "나 왔어", "아이고, 그려. 저런, 얼른 앉으렴.")
        added = system_of(dialogue)[len(system_of(base)):]
        for quirk in quirks:
            self.assertIn(quirk, added)

    async def test_the_avoidance_instruction_stays_short(self):
        quirks = ["가" * QUIRK_MAX_LENGTH, "나" * QUIRK_MAX_LENGTH, "다" * QUIRK_MAX_LENGTH]
        persona = PERSONA.replace('- "아이고", "그려" 같은',
                                  "- " + ", ".join(f'"{q}"' for q in quirks) + " 같은")
        dialogue, base = make(persona), make(persona)
        self.assertEqual(dialogue.quirks, quirks)
        deliver(dialogue, "나 왔어", " ".join(f"{q}." for q in quirks))
        added = system_of(dialogue)[len(system_of(base)):]
        # 가장 긴 등록 형태(60자 × 3개)에서도 전부 담고 상한 안에 머문다.
        self.assertEqual(sum(1 for quirk in quirks if quirk in added), 3)
        self.assertLess(len(added), 400)

    async def test_only_answers_the_user_actually_heard_are_counted(self):
        dialogue, base = make(tts=object()), make()
        plain = system_of(base)
        for _ in range(3):
            deliver(dialogue, "말해줘", "아이고, 그랬구나.", spoken=0)
        self.assertEqual([m["role"] for m in dialogue.history], ["user"] * 3)
        self.assertEqual(system_of(dialogue), plain)
        deliver(dialogue, "말해줘", "아이고, 그랬구나.", spoken=len("아이고, 그랬구나."))
        self.assertNotEqual(system_of(dialogue), plain)

    async def test_a_user_asking_about_the_phrase_is_not_blocked(self):
        dialogue, base = make(), make()
        deliver(dialogue, "나 왔어", "아이고, 어서 오렴.")
        self.assertNotEqual(system_of(dialogue), system_of(base))
        heard = "할머니 '아이고' 그 말 한 번만 더 해줘."
        self.assertEqual(system_of(dialogue, heard), system_of(base, heard))

    async def test_reset_clears_repetition_and_connections_stay_separate(self):
        dialogue, other = make(), make()
        deliver(dialogue, "나 왔어", "아이고, 어서 오렴.")
        self.assertEqual(system_of(other), system_of(make()))   # 다른 연결은 물들지 않는다
        self.assertNotEqual(system_of(dialogue), system_of(other))
        await dialogue.reset()
        self.assertEqual(system_of(dialogue), system_of(other))


if __name__ == "__main__":
    unittest.main()
