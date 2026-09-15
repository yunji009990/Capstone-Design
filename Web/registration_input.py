"""웹 설문과 인물 확인 결과가 같은 입력에서 만들어졌는지 확인한다."""
import hashlib
import hmac
import json


def parse_survey(raw):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 131072:
        raise ValueError("설문 내용이 너무 깁니다.")
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise ValueError("설문을 작성한 뒤 인물 설정을 만들어 주세요.") from None
    if not isinstance(data, dict):
        raise ValueError("설문 형식이 올바르지 않습니다.")
    for field in ("relation", "sex", "honorific", "calls_user", "user_name",
                  "missed_moment", "unsaid_words", "wished_to_hear", "tone_setting"):
        if field in data and not isinstance(data[field], str):
            raise ValueError("설문의 문자 입력을 확인해 주세요.")
    if not data.get("relation", "").strip():
        raise ValueError("관계를 적은 뒤 인물 설정을 만들어 주세요.")
    for field in ("personality_traits", "speech_quirks", "shared_memories"):
        if field in data and (not isinstance(data[field], list)
                              or any(not isinstance(item, str) for item in data[field])):
            raise ValueError("설문의 성격·말버릇·추억 입력을 확인해 주세요.")
    return data


def survey_revision(data):
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def registration_survey(raw, revision, persona, session=""):
    if session.strip():
        raise ValueError("세션 ID는 서버가 자동으로 발급합니다. 페이지를 새로 열어 등록해 주세요.")
    data = parse_survey(raw)
    if not isinstance(revision, str) or not hmac.compare_digest(
            survey_revision(data).encode("ascii"), revision.encode("utf-8")):
        raise ValueError("설문이 바뀌었거나 인물 설정이 준비되지 않았습니다. 인물 확인에서 다시 작성해 주세요.")
    if not isinstance(persona, str) or not persona.strip():
        raise ValueError("인물 설정이 비어 있습니다. 설문으로 인물 설정을 먼저 만들어 주세요.")
    return data
