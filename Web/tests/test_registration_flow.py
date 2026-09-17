"""웹 등록 회귀 검사. 실제 경로 함수·로컬 등록 API를 쓰고 음성 변환·Tripo만 대체한다."""
import ast
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import wave
from unittest.mock import Mock

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Server"))
sys.path.insert(0, str(ROOT / "Survey"))
from Web import survey_v2
from core import database
from registration.app import create_app

PRESETS = json.loads((ROOT / "Web/static/presets_v2.json").read_text(encoding="utf-8"))["profiles"]


def preset(key):
    """화면 테스트 버튼과 같은 가상 자료를 쓴다. 실제 인물이 아니다."""
    return copy.deepcopy(next(p for p in PRESETS if p["key"] == key)["answers"])


def audio():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(bytes(16000 * 2 * 3))
    return stream.getvalue()


class SurveyCompileTests(unittest.TestCase):
    """설문 원문 → 인물·사전지식·규칙 변환의 계약."""

    def compiled(self, answers):
        return survey_v2.compile_survey(survey_v2.validate(answers))

    def test_two_preset_profiles_compile_without_issues(self):
        for profile in PRESETS:
            with self.subTest(profile=profile["key"]):
                result = self.compiled(profile["answers"])
                self.assertEqual(result["issues"], [])
                self.assertIn("[대화 예시]", result["persona"])
                self.assertLessEqual(result["ai_text_chars"], survey_v2.MAX_AI_TEXT)

    def test_user_name_never_appears_in_character_self_introduction(self):
        answers = preset("grandmother")
        answers["person"]["user_name"] = "지훈"
        answers["person"]["person_name"] = "박순자"
        result = self.compiled(answers)
        persona = result["persona"]
        self.assertNotIn("지훈", persona)
        self.assertIn("박순자", persona)
        self.assertIn('사용자의 이름은 "지훈"이다', result["knowledge"])

    def test_missing_person_name_uses_relation_without_inventing_one(self):
        answers = preset("grandmother")
        answers["person"]["person_name"] = ""
        result = self.compiled(answers)
        self.assertNotIn("박순자", result["persona"] + result["knowledge"])
        self.assertIn("할머니", result["persona"])
        self.assertTrue(any("이름을 비워" in notice for notice in result["review"]["notices"]))

    def test_speech_form_decides_endings_for_every_relation(self):
        expected = {"informal": "반말로 말합니다", "formal": "존댓말로 말합니다",
                    "mixed": "반말과 존댓말을 함께", "unknown": "부드러운 해요체"}
        for relation in ("어머니", "친구", "아들", "옛 스승"):
            for form, text in expected.items():
                with self.subTest(relation=relation, form=form):
                    answers = preset("friend")
                    answers["person"]["relation"] = relation
                    answers["speech"]["form"] = form
                    answers["speech"]["mixed_note"] = "정색하실 때만 존댓말"
                    persona = self.compiled(answers)["persona"]
                    self.assertIn(text, persona)
                    if form in ("formal", "unknown"):
                        self.assertIn('"-어요"', persona)

    def test_unknown_form_is_announced_and_never_stored_as_a_real_style(self):
        answers = preset("friend")
        answers["speech"]["form"] = "unknown"
        result = self.compiled(answers)
        self.assertTrue(any("해요체" in notice for notice in result["review"]["notices"]))
        self.assertIn("해요체", result["rules"])
        style = next(item for item in result["review"]["sections"][3]["items"]
                     if item["question"] == "7-2")
        self.assertEqual(style["value"], "잘 모름")

    def test_style_conflict_is_reported_without_rewriting_the_original_text(self):
        answers = preset("friend")
        answers["speech"]["form"] = "formal"
        result = self.compiled(answers)
        self.assertTrue(any("됐고" in notice for notice in result["review"]["notices"]))
        self.assertIn("됐고", result["persona"])          # 원문을 고치지 않는다

    def test_wish_new_information_and_uncertainty_stay_separated(self):
        result = self.compiled(preset("grandmother"))
        knowledge = result["knowledge"]
        self.assertIn("다시 듣고 싶어 하는 말이 있다", knowledge)
        self.assertIn("이번에 새로 알려준 소식", knowledge)
        self.assertIn("예전부터 알고 있던 일이 아니다", knowledge)
        self.assertIn("사용자가 대략 기억", knowledge)
        self.assertIn("사용자가 정확히 기억", knowledge)

    def test_a_wish_is_marked_unverified_instead_of_denied(self):
        """듣고 싶은 말은 '하지 않았다'가 아니라 '확인되지 않았다'로 전달한다."""
        result = self.compiled(preset("grandmother"))
        knowledge = result["knowledge"]
        self.assertIn("사용자의 바람이다. 실제 과거 발언인지 확인되지 않았으므로", knowledge)
        for denial in ("실제로 한 말이 아니다", "한 적이 없다", "말한 적 없다"):
            self.assertNotIn(denial, knowledge)
        # 실제 기록이 있는 대사와 카드는 그대로 남는다.
        self.assertIn('그때 당신이 한 말 "씨는 여기다 뱉어라"', knowledge)
        self.assertIn('"밥은 먹고 다니니?"', result["persona"])
        wish = next(item for section in result["review"]["sections"]
                    for item in section["items"] if item["question"] == "6-3")
        self.assertIn("확인되지 않은", wish["note"])
        self.assertNotIn("실제로 한 말로 쓰지 않습니다", wish["note"])

    def one_card(self, kind, **card):
        """한 종류의 카드 하나만 남긴 설문. 대상·집계 검사를 서로 섞지 않는다."""
        answers = preset("friend")
        answers["sections"] = {name: {"state": "answered", "cards": []}
                               for name in survey_v2.SECTIONS}
        answers["sections"][kind]["cards"] = [{"id": "c1", "kind": kind,
                                               "content": "검사용 내용", **card}]
        return answers

    def test_every_selectable_subject_is_kept_in_the_final_knowledge(self):
        expected = {
            ("background", "person"): "당신의 생활 배경",
            ("shared_memory", "both"): "사용자와 당신이 함께 겪은 일",
            ("preference", "person"): "당신의 좋아하던 것",
            ("preference", "user"): "사용자의 좋아하던 것",
            ("preference", "both"): "사용자와 당신 둘 다의 좋아하던 것",
            ("person", "other"): "함께 아는 다른 사람. 당신도 사용자도 아니다",
            ("place_activity", "both"): "사용자와 당신이 자주 가거나",
            ("about_user", "user"): "당신이 예전부터 알던 사용자의 모습",
            ("news", "user"): "사용자 자신의 일",
            ("news", "other"): "다른 사람의 일이며 사용자나 당신의 일이 아님",
        }
        for kind, meta in survey_v2.SECTIONS.items():
            for subject in meta["subjects"]:
                with self.subTest(kind=kind, subject=subject):
                    extra = {"category": "like"} if kind == "preference" else {}
                    answers = self.one_card(kind, subject=subject, **extra)
                    self.assertIn(expected[(kind, subject)], self.compiled(answers)["knowledge"])

    def test_a_subject_the_question_does_not_offer_is_rejected(self):
        for kind, meta in survey_v2.SECTIONS.items():
            for subject in set(survey_v2.SUBJECTS) - set(meta["subjects"]):
                with self.subTest(kind=kind, subject=subject):
                    extra = {"category": "like"} if kind == "preference" else {}
                    answers = self.one_card(kind, subject=subject, **extra)
                    with self.assertRaises(ValueError) as caught:
                        survey_v2.validate(answers)
                    self.assertIn(meta["question"], str(caught.exception))

    def test_another_persons_card_never_becomes_the_character_or_the_user(self):
        answers = self.one_card("person", content="영자",
                                relation_to_person="옆집 친구", relation_to_user="이웃 어르신")
        knowledge = self.compiled(answers)["knowledge"]
        self.assertIn("영자는 사용자와 당신이 함께 아는 다른 사람", knowledge)
        self.assertIn("당신도 사용자도 아니다", knowledge)
        self.assertNotIn("당신의 이름은 \"영자\"", knowledge)

    def test_consent_must_be_real_booleans_and_all_true(self):
        for value in ({}, {"image": True}, {"image": True, "voice": True},
                      {"image": True, "voice": True, "understand": True, "extra": True},
                      {"image": True, "voice": True, "understand": "false"},
                      {"image": True, "voice": True, "understand": "true"},
                      {"image": True, "voice": 1, "understand": True},
                      {"image": True, "voice": True, "understand": 0},
                      {"image": True, "voice": True, "understand": [True]},
                      {"image": True, "voice": True, "understand": None},
                      None, "true", [True, True, True]):
            with self.subTest(consent=value):
                answers = preset("friend")
                answers["consent"] = value
                with self.assertRaisesRegex(ValueError, "동의"):
                    survey_v2.validate(answers)
        answers = preset("friend")
        answers["consent"] = {"image": True, "voice": True, "understand": False}
        with self.assertRaisesRegex(ValueError, "동의 세 가지"):
            survey_v2.validate(answers)

    def test_budget_counts_every_free_text_that_reaches_the_model(self):
        """이름·호칭·관계·성격·말투 설명도 프롬프트에 들어가므로 같은 집계로 센다."""
        base = self.compiled(preset("friend"))["ai_text_chars"]
        for field, value in (("person_name", "가" * 30), ("user_name", "가" * 30),
                             ("relation", "가" * 30), ("user_calls_person", "가" * 30),
                             ("person_calls_user", "가" * 30), ("era", "가" * 30)):
            with self.subTest(field=field):
                answers = preset("friend")
                grown = len(answers["person"][field])
                answers["person"][field] = value
                self.assertEqual(self.compiled(answers)["ai_text_chars"], base + 30 - grown)
        for path, field in (("person", "traits"), ("speech", "mixed_note"), ("speech", "dialect")):
            with self.subTest(field=field):
                answers = preset("friend")
                previous = answers[path][field]
                answers[path][field] = ["가" * 20] if field == "traits" else "가" * 20
                shrunk = sum(len(item) for item in previous) if field == "traits" else len(previous)
                self.assertEqual(self.compiled(answers)["ai_text_chars"], base + 20 - shrunk)
        excluded = preset("friend")
        for card in excluded["sections"]["shared_memory"]["cards"]:
            card["mention_policy"] = "exclude_ai"
        self.assertLess(self.compiled(excluded)["ai_text_chars"], base)

    def test_budget_limit_matches_the_measured_token_ceiling(self):
        self.assertEqual(survey_v2.MAX_AI_TEXT, 800)
        for profile in PRESETS:
            with self.subTest(profile=profile["key"]):
                self.assertLessEqual(self.compiled(profile["answers"])["ai_text_chars"], 800)

    def test_long_single_field_advice_does_not_promise_that_excluding_helps(self):
        answers = preset("friend")
        answers["sections"]["shared_memory"]["cards"][0]["content"] = "가" * 201
        answers["sections"]["shared_memory"]["cards"][0]["mention_policy"] = "exclude_ai"
        issues = self.compiled(answers)["issues"]
        self.assertEqual([issue["code"] for issue in issues], ["content_too_long"])
        self.assertIn("지워 주세요", issues[0]["message"])
        self.assertNotIn("전달하지 않음", issues[0]["message"])

    def test_the_character_never_blames_the_user_for_asking_again(self):
        for key in ("grandmother", "friend"):
            with self.subTest(profile=key):
                persona = self.compiled(preset(key))["persona"]
                self.assertIn("기억을 탓하거나", persona)
                self.assertIn("다시 말해", persona)
                examples = persona.split("[대화 예시]")[1]
                self.assertIn("아까 그거 뭐라고 했지?", examples)
                self.assertNotIn("왜, 궁금했", persona)
                self.assertNotIn("어떻게 잊어", examples)

    def test_two_word_habits_are_not_pasted_into_the_greeting_example(self):
        answers = preset("friend")                     # 말버릇 "야 진짜"
        examples = self.compiled(answers)["persona"].split("[대화 예시]")[1]
        self.assertNotIn("야 진짜,", examples)
        self.assertIn("야 진짜", self.compiled(answers)["persona"])   # 말투 목록에는 남는다
        single = preset("grandmother")                 # 말버릇 "아이고"
        self.assertIn("아이고,", self.compiled(single)["persona"].split("[대화 예시]")[1])

    def test_excluded_cards_never_reach_persona_knowledge_rules_or_examples(self):
        answers = preset("grandmother")
        secret = answers["sections"]["news"]["cards"][1]
        self.assertEqual(secret["mention_policy"], "exclude_ai")
        result = self.compiled(answers)
        for text in (result["persona"], result["knowledge"], result["rules"]):
            self.assertNotIn("재검사", text)
        excluded = result["review"]["sections"][4]["items"]
        self.assertTrue(any("재검사" in item["value"] for item in excluded))

    def test_conditional_mention_reaches_rules_but_declined_topics_keep_their_content(self):
        result = self.compiled(preset("grandmother"))
        self.assertIn("먼저 꺼낼 때만", result["knowledge"])
        self.assertIn("먼저 말하지 않습니다", result["rules"])
        self.assertIn("병원에서의 마지막 며칠", result["rules"])
        # 가상 예시 두 인물은 모두 답이 채워져 있으므로 '답하고 싶지 않음'은 여기서 직접 만든다.
        declined = preset("friend")
        declined["sections"]["about_user"] = {"state": "declined", "cards": []}
        rules = self.compiled(declined)["rules"]
        self.assertIn("답하지 않기로 한 항목", rules)
        self.assertIn("그분이 알고 있던 나의 모습", rules)

    def test_empty_and_unknown_sections_do_not_become_absent_facts(self):
        # '모름'도 예시 자료가 아니라 이 검사가 직접 만든 상태로 확인한다.
        answers = preset("friend")
        answers["sections"]["person"] = {"state": "unknown", "cards": []}
        result = self.compiled(answers)
        self.assertNotIn("함께 아는 사람이 없다", result["knowledge"])
        self.assertIn("없는 일로 단정하지 않고", result["rules"])
        answers["sections"]["person"]["state"] = "none"
        stated = self.compiled(answers)["knowledge"]
        self.assertIn("실제로 없다고 알려주었다", stated)

    def test_same_answers_always_compile_to_the_same_revisions(self):
        first, second = self.compiled(preset("friend")), self.compiled(preset("friend"))
        self.assertEqual(first["preview_revision"], second["preview_revision"])
        changed = preset("friend")
        changed["person"]["relation"] = "형"
        third = self.compiled(changed)
        self.assertNotEqual(first["survey_revision"], third["survey_revision"])
        self.assertNotEqual(first["preview_revision"], third["preview_revision"])

    def test_long_input_is_reported_instead_of_being_silently_trimmed(self):
        answers = preset("friend")
        answers["sections"]["shared_memory"]["cards"][0]["content"] = "가" * 201
        result = self.compiled(answers)
        self.assertEqual([issue["code"] for issue in result["issues"]], ["content_too_long"])
        self.assertIn("가" * 201, result["knowledge"])          # 잘라내지 않는다
        answers = preset("friend")
        answers["sections"]["shared_memory"]["cards"][0]["content"] = "가" * 200
        answers["heart"]["missed_moment"] = "나" * 200
        for index in range(9):
            answers["sections"]["preference"]["cards"].append(
                {"id": f"x{index}", "kind": "preference", "content": "다" * 200,
                 "category": "like", "subject": "person", "certainty": "exact",
                 "mention_policy": "proactive"})
        self.assertIn("knowledge_too_long",
                      [issue["code"] for issue in self.compiled(answers)["issues"]])

    def test_invalid_states_kinds_and_broken_cards_are_rejected(self):
        broken = [
            ({"survey_schema_version": 1}, "다시 작성"),
            ({"sections": {"unknown_kind": {"state": "answered", "cards": []}}}, "알 수 없는"),
            ({"sections": {"news": {"state": "maybe", "cards": []}}}, "상태"),
            ({"sections": {"news": {"state": "unknown", "cards": [
                {"id": "c1", "kind": "news", "content": "남은 카드"}]}}}, "카드가 남아"),
            ({"sections": {"news": {"state": "answered", "cards": [
                {"id": "c1", "kind": "shared_memory", "content": "종류 불일치"}]}}}, "카드 종류"),
            ({"sections": {"news": {"state": "answered", "cards": [
                {"id": "c1", "kind": "news", "content": ""}]}}}, "카드를 지워"),
            ({"sections": {"news": {"state": "answered", "cards": [
                {"id": "c1", "kind": "news", "content": "값", "certainty": "매우"}]}}}, "알 수 없는 카드 항목"),
            ({"sections": {"shared_memory": {"state": "answered", "cards": [
                {"id": "c1", "kind": "shared_memory", "content": "값", "certainty": "매우"}]}}}, "기억 정도"),
            ({"person": {"relation": " "}}, "관계를 적은"),
            ({"consent": {"image": True, "voice": False, "understand": True}}, "동의"),
            ({"speech": {"form": "polite"}}, "말투"),
        ]
        for patch, message in broken:
            with self.subTest(patch=list(patch)):
                answers = preset("friend")
                for key, value in patch.items():
                    if isinstance(value, dict) and isinstance(answers.get(key), dict):
                        answers[key].update(value)
                    else:
                        answers[key] = value
                with self.assertRaises(ValueError) as caught:
                    survey_v2.validate(answers)
                self.assertIn(message, str(caught.exception))

    # ── 4-11·4-12 떠나신 경위 ────────────────────────────────────
    # 고인이라는 기본 사실은 대화 서버의 [재회] 규칙이 맡는다(Server/persona_context.py).
    # 여기서 검사하는 것은 **개별 원인·시점**이 적은 만큼만 전달되는지다.
    def passing(self, **values):
        answers = preset("grandmother")
        answers["passing"] = {"state": "answered", "cause": "", "time": "",
                              "mention_policy": "on_request", **values}
        return answers

    def test_cause_of_death_reaches_knowledge_only_when_the_user_wrote_it(self):
        told = self.compiled(self.passing(cause="오래 앓으시다 돌아가셨습니다", time="재작년 겨울"))
        self.assertIn("세상을 떠난 경위는 사용자가 알려준 대로", told["knowledge"])
        self.assertIn("오래 앓으시다 돌아가셨습니다", told["knowledge"])
        self.assertIn('세상을 떠난 시점은 "재작년 겨울"이다', told["knowledge"])
        self.assertIn("사용자가 꺼낼 때만", told["rules"])
        blank = self.compiled(self.passing())
        self.assertNotIn("세상을 떠난", blank["knowledge"])
        self.assertNotIn("세상을 떠난", blank["rules"])

    def test_unknown_or_declined_passing_never_becomes_an_absent_death(self):
        for state in ("unknown", "declined"):
            with self.subTest(state=state):
                result = self.compiled(self.passing(state=state))
                text = result["persona"] + result["knowledge"] + result["rules"]
                self.assertNotIn("세상을 떠난 경위", text)
                # 경위를 모르는 것이 '떠나지 않았다'가 되지 않는다. 전제는 그대로 알린다.
                self.assertTrue(any("이미 세상을 떠난 분" in n for n in result["review"]["notices"]))
                # 안내는 **전달·설정 사실**만 말한다. 생성 답변을 보장하지 않는다.
                self.assertTrue(any("AI에 전달하지 않고 모르는 것으로 설정합니다" in item["note"]
                                    for section in result["review"]["sections"]
                                    for item in section["items"] if item["question"] == "4-11"))

    def test_there_is_no_option_that_denies_the_death(self):
        self.assertNotIn("none", survey_v2.PASSING_STATES)
        self.assertNotIn("proactive", survey_v2.PASSING_MENTION)
        with self.assertRaises(survey_v2.SurveyError):
            survey_v2.validate(self.passing(state="none"))

    def test_excluded_passing_leaves_every_generated_text_and_the_budget(self):
        hidden = self.compiled(self.passing(cause="교통사고였습니다", time="작년 봄",
                                            mention_policy="exclude_ai"))
        for text in (hidden["persona"], hidden["knowledge"], hidden["rules"]):
            self.assertNotIn("교통사고", text)
            self.assertNotIn("작년 봄", text)
        excluded = hidden["review"]["sections"][4]["items"]
        self.assertTrue(any("교통사고였습니다" == item["value"] for item in excluded))
        told = self.compiled(self.passing(cause="교통사고였습니다", time="작년 봄"))
        self.assertEqual(hidden["ai_text_chars"],
                         told["ai_text_chars"] - len("교통사고였습니다") - len("작년 봄"))

    def test_a_leftover_exclude_choice_does_not_hide_the_chosen_state(self):
        """적은 내용이 없으면 감출 것도 없다. 고른 상태는 확인 화면에 남아야 한다."""
        result = self.compiled(self.passing(state="declined", mention_policy="exclude_ai"))
        shown = [item for section in result["review"]["sections"]
                 for item in section["items"] if item["question"] == "4-11"]
        self.assertEqual([item["value"] for item in shown], ["답하고 싶지 않음"])
        # 적은 내용이 없으니 'AI에 전달하지 않는 내용'에는 이 문항이 실리지 않는다.
        self.assertEqual([item for item in result["review"]["sections"][4]["items"]
                          if item["question"] == "4-11"], [])

    def test_a_state_that_is_not_answered_may_not_carry_written_content(self):
        with self.assertRaises(survey_v2.SurveyError):
            survey_v2.validate(self.passing(state="unknown", cause="교통사고"))

    def test_surveys_without_the_new_field_still_compile_as_unknown(self):
        answers = preset("grandmother")
        answers.pop("passing", None)
        result = survey_v2.validate(answers)
        self.assertEqual(result["passing"],
                         {"state": "unknown", "cause": "", "time": "", "mention_policy": "on_request"})
        # 정규화 결과에 칸이 생기므로 해시는 달라진다. 오래 열어 둔 화면은 다시 확인해야 한다.
        self.assertNotEqual(survey_v2.survey_revision(result), survey_v2.survey_revision(answers))

    def test_the_compiler_version_moved_so_old_previews_must_be_confirmed_again(self):
        self.assertEqual(survey_v2.COMPILER_VERSION, "survey_v2_compile_3")

    def test_a_non_string_state_is_a_survey_error_not_a_server_crash(self):
        """list·dict 가 오면 `in` 이 TypeError 를 내 400 대신 500 이 나간다."""
        for broken in ([], {}, 3, True, None):
            with self.subTest(state=repr(broken)):
                with self.assertRaises(survey_v2.SurveyError):
                    survey_v2.validate(self.passing(state=broken))
        for field in ("cause", "time", "mention_policy"):
            with self.subTest(field=field):
                with self.assertRaises(survey_v2.SurveyError):
                    survey_v2.validate(self.passing(**{field: []}))

    def test_no_example_asks_what_the_character_is_doing_now(self):
        """인물이 사실대로 답할 수 없는 질문을 말투 본보기로 주지 않는다.

        2026-09-17 실측에서 예전 '일상' 예문을 모델이 그대로 베껴 세상을 떠난 인물이
        근황 질문 답변이 두 차례 실행 각각 9/9(3인물 × 3회)로 떠난 뒤의 생활을 말했고,
        그중 이 예문과 거의 같은 문장이 실행마다 2건씩 있었다."""
        for examples in list(survey_v2.CASUAL_EXAMPLES.values()) + [survey_v2.POLITE_EXAMPLES]:
            for user, answer in examples:
                self.assertNotIn("뭐 하고 있었", user)
                for invented in ("텔레비전 보고 있었", "폰 보고 있었", "그냥 있었지 뭐"):
                    self.assertNotIn(invented, answer)
        for answers in (preset("grandmother"), preset("friend")):
            persona = self.compiled(answers)["persona"]
            self.assertNotIn("텔레비전 보고 있었", persona)
            self.assertNotIn("폰 보고 있었", persona)
        # 톤 설명도 지금 생활을 이어가는 것으로 읽히지 않게 둔다.
        self.assertNotIn("생전에 하던 그대로", survey_v2.TONES["casual_recreation"])

    def test_the_bereavement_weeks_still_never_reach_the_model(self):
        """사별 경과 주로 정확한 사망일을 역산하지 않는다. 시점은 4-12로만 받는다."""
        texts = set()
        for weeks in (None, 1, 52, 520):
            answers = preset("grandmother")
            answers["bereavement_weeks"] = weeks
            result = self.compiled(answers)
            texts.add(result["persona"] + result["knowledge"] + result["rules"])
        self.assertEqual(len(texts), 1)

    def test_over_long_cause_is_reported_even_when_it_is_excluded(self):
        result = self.compiled(self.passing(cause="가" * 201, mention_policy="exclude_ai"))
        self.assertIn("content_too_long", [issue["code"] for issue in result["issues"]])
        self.assertIn("떠나신 경위", " ".join(issue["message"] for issue in result["issues"]))

    def test_duplicate_card_ids_are_rejected_across_sections(self):
        answers = preset("grandmother")
        answers["sections"]["preference"]["cards"][0]["id"] = \
            answers["sections"]["shared_memory"]["cards"][0]["id"]
        with self.assertRaisesRegex(ValueError, "겹칩니다"):
            survey_v2.validate(answers)


class WebRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        provider = TestClient(create_app(self.root / "people", "", "reader"))
        self.provider = provider
        self.addCleanup(provider.close)
        old_db = database.DB_PATH
        database.DB_PATH = self.root / "survey.db"
        self.addCleanup(setattr, database, "DB_PATH", old_db)
        def directory(sid):
            dest = self.root / "assets" / sid
            dest.mkdir(parents=True, exist_ok=True)
            return dest
        self.post = Mock(side_effect=lambda url, **kw: provider.post("/session/start", data=kw["data"], files=kw["files"]))
        self.convert = Mock(return_value=(audio(), 3.0, {}))
        app = FastAPI()
        self.scope = dict(app=app, Form=Form, File=File, UploadFile=UploadFile, HTTPException=HTTPException,
            os=os, json=json, survey_v2=survey_v2,
            survey_db=database, survey_store=types.SimpleNamespace(session_dir=directory),
            survey_jobs=types.SimpleNamespace(dispatch_model_job=Mock(return_value="queued")),
            httpx=types.SimpleNamespace(post=self.post), SESSION_URL="http://local", _headers=lambda: {},
            _to_wav24=self.convert, LAST_REF=str(self.root / "last.wav"), ALLOWED={".wav"})
        # Web.app의 무거운 음성 라이브러리·환경 로딩을 실행하지 않고, 수정 대상 함수 그대로 라우팅한다.
        tree = ast.parse((ROOT / "Web/app.py").read_text(encoding="utf-8"))
        names = {"_record_and_model", "persona_from_survey", "publish_direct"}
        selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        self.assertEqual(len(selected), len(names))
        exec(compile(ast.Module(body=selected, type_ignores=[]), "Web/app.py", "exec"), self.scope)
        self.web = TestClient(app)
        self.addCleanup(self.web.close)
        self.answers = preset("grandmother")

    def draft(self, answers=None):
        raw = json.dumps(answers or self.answers, ensure_ascii=False)
        response = self.web.post("/persona", data={"survey": raw})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        return {"survey": raw, "survey_revision": body["survey_revision"],
                "preview_revision": body["preview_revision"], "body": body}

    def submit(self, form, image=False):
        form = {key: value for key, value in form.items() if key != "body"}
        files = {"voice": ("voice.wav", audio())}
        if image:
            files["image"] = ("front.png", b"synthetic image")
        return self.web.post("/publish_direct", data=form, files=files)

    def test_voice_route_registers_server_compiled_text_with_new_ids_and_snapshot(self):
        form = self.draft()
        compiled = form["body"]
        ids = []
        for _ in range(2):          # 같은 설문을 다시 올려도 세션 ID 는 새로 발급된다
            result = self.submit(form, image=True)
            self.assertEqual(result.status_code, 200, result.text)
            item = result.json()
            ids.append(item["session"])
            self.assertTrue(item["survey_saved"])
            self.assertTrue(item["model_queued"])
            self.assertNotIn("session", self.post.call_args.kwargs["data"])
            bundle = self.provider.get(f"/internal/personas/{item['session']}",
                                       headers={"X-Persona-Token": "reader"}).json()
            self.assertEqual(bundle["schema_version"], 1)
            self.assertEqual(bundle["persona"], compiled["persona"])
            self.assertEqual(bundle["knowledge"], compiled["knowledge"])
            self.assertEqual(bundle["rules"], compiled["rules"])
            self.assertTrue(bundle["voice_sha256"] and bundle["revision"])
            payload = database.get_session(item["session"])["payload"]
            self.assertEqual(payload["survey_schema_version"], 2)
            self.assertEqual(payload["answers"], survey_v2.validate(self.answers))
            snapshot = payload["compiled_snapshot"]
            self.assertEqual(snapshot["preview_revision"], compiled["preview_revision"])
            self.assertEqual(snapshot["persona"], compiled["persona"])
            self.assertEqual(snapshot["compiler_version"], survey_v2.COMPILER_VERSION)
        self.assertNotEqual(*ids)

    def test_speaker_separation_routes_are_gone(self):
        """화자 분리 경로는 소스에 없고 격리된 API에 요청하면 404 다."""
        source = (ROOT / "Web/app.py").read_text(encoding="utf-8")
        for route in ('"/extract"', '"/extract/{job}"', '"/file/{job}/{path:path}"', '"/publish"'):
            self.assertNotIn(route, source)
        for path in ("/extract", "/file/job/ref.wav", "/publish"):
            with self.subTest(path=path):
                self.assertEqual(self.web.post(path, data={}).status_code, 404)
                self.assertEqual(self.web.get(path).status_code, 404)

    def test_excluded_material_is_stored_but_never_sent_to_the_dialogue_service(self):
        form = self.draft()
        result = self.submit(form).json()
        sent = self.post.call_args.kwargs["data"]
        for value in sent.values():
            self.assertNotIn("재검사", value)
        payload = database.get_session(result["session"])["payload"]
        self.assertIn("재검사", json.dumps(payload["answers"], ensure_ascii=False))

    def test_stale_unconfirmed_long_or_client_named_registration_is_rejected_before_audio_work(self):
        good = self.draft()
        long_answers = preset("grandmother")
        long_answers["sections"]["shared_memory"]["cards"][0]["content"] = "가" * 201
        raw_long = json.dumps(long_answers, ensure_ascii=False)
        compiled_long = survey_v2.compile_survey(survey_v2.validate(long_answers))
        cases = [
            {**good, "survey_revision": ""},
            {**good, "preview_revision": ""},
            {**good, "preview_revision": "0" * 64},
            {**good, "survey": json.dumps({**self.answers, "person":
                {**self.answers["person"], "relation": "어머니"}}, ensure_ascii=False)},
            {**good, "session": "old-id"},
            {"survey": raw_long, "survey_revision": compiled_long["survey_revision"],
             "preview_revision": compiled_long["preview_revision"]},
            {"survey": json.dumps({"relation": "할머니"}), "survey_revision": "x", "preview_revision": "y"},
        ]
        for form in cases:
            with self.subTest(form=list(form)):
                self.assertEqual(self.submit(form).status_code, 400)
        self.post.assert_not_called()
        self.convert.assert_not_called()

    def test_old_v1_screens_are_refused_with_a_clear_message(self):
        legacy = {"relation": "할머니", "user_name": "가상사용자", "shared_memories": ["가상 추억"],
                  "consent_image": True, "consent_voice": True, "consent_understand": True}
        response = self.web.post("/persona", data={"survey": json.dumps(legacy, ensure_ascii=False)})
        self.assertEqual(response.status_code, 400)
        self.assertIn("다시 작성", response.json()["detail"])

    def test_changed_survey_gets_new_revisions_and_new_text(self):
        first = self.draft()
        changed = preset("grandmother")
        changed["person"]["relation"] = "어머니"
        changed["sections"]["shared_memory"]["cards"][0]["content"] = "새 추억"
        second = self.draft(changed)
        self.assertNotEqual(first["survey_revision"], second["survey_revision"])
        self.assertNotEqual(first["preview_revision"], second["preview_revision"])
        self.assertIn("어머니", second["body"]["persona"])
        self.assertIn("새 추억", second["body"]["knowledge"])
        self.assertEqual(self.submit(second).status_code, 200)

    def test_review_lists_question_numbers_for_every_shown_item(self):
        review = self.draft()["body"]["review"]
        items = [item for section in review["sections"] for item in section["items"]]
        self.assertTrue(items)
        for item in items:
            self.assertRegex(item["question"], r"^\d+-\d+$")

    def test_survey_write_failure_is_reported_and_does_not_queue_a_model(self):
        insert = Mock(side_effect=RuntimeError("synthetic storage failure"))
        self.scope["survey_db"] = types.SimpleNamespace(insert_session=insert)
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.submit(self.draft(), image=True).json()
        self.assertFalse(result["survey_saved"])
        self.assertFalse(result["model_queued"])
        self.assertIn("설문 기록", result["registration_warning"])
        self.scope["survey_jobs"].dispatch_model_job.assert_not_called()

    def test_model_queue_failure_is_distinct_from_successful_person_and_survey(self):
        self.scope["survey_jobs"].dispatch_model_job.side_effect = RuntimeError("synthetic queue failure")
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.submit(self.draft(), image=True).json()
        self.assertTrue(result["survey_saved"])
        self.assertFalse(result["model_queued"])
        self.assertIn("모델 작업", result["registration_warning"])

    def test_v1_rows_stay_readable_and_deletion_removes_answers_and_snapshot(self):
        database.insert_session(session_id="legacy-1", payload={"relation": "할머니"},
                                consent_image=True, consent_voice=True, consent_understand=True,
                                bereavement_weeks=10, has_image=False, has_voice=True)
        self.assertEqual(database.get_session("legacy-1")["payload"]["relation"], "할머니")
        result = self.submit(self.draft()).json()
        database.soft_delete(result["session"])
        self.assertEqual(database.get_session(result["session"])["payload"], {})
        self.assertEqual(database.get_session("legacy-1")["payload"]["relation"], "할머니")

    def test_browser_script_regressions(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "웹 화면 회귀 검사에는 Node.js 22 이상이 필요합니다.")
        result = subprocess.run([node, "--test", "--test-reporter=tap", str(Path(__file__).with_suffix(".cjs"))],
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("# fail 0", result.stdout)


if __name__ == "__main__":
    unittest.main()
