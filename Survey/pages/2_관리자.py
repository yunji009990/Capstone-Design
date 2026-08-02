"""관리자 페이지 — 세션 목록·상세·페르소나 프롬프트 확인·다운로드·삭제."""
from __future__ import annotations

import json

import streamlit as st

from core import database, jobs, meshy, prompt_builder, storage, styling

st.set_page_config(page_title="다시, 봄 — 관리자", page_icon="🔐", layout="wide")
styling.inject()

st.title("🔐 관리자")

# ── 비밀번호 게이트 ───────────────────────────────────────────
if "admin_authed" not in st.session_state:
    st.session_state.admin_authed = False

if not st.session_state.admin_authed:
    pwd = st.text_input("관리자 비밀번호", type="password")
    if st.button("로그인"):
        expected = st.secrets.get("admin_password", "dasibom-admin")
        if pwd == expected:
            st.session_state.admin_authed = True
            st.rerun()
        else:
            st.error("비밀번호가 다릅니다.")
    st.stop()

# ── 인증 후 ────────────────────────────────────────────────────
st.success("관리자 모드")
if st.button("로그아웃"):
    st.session_state.admin_authed = False
    st.rerun()

show_deleted = st.checkbox("폐기된 세션도 보기", value=False)
sessions = database.list_sessions(include_deleted=show_deleted)

st.caption(f"총 {len(sessions)}건")
st.divider()

if not sessions:
    st.info("아직 제출된 세션이 없습니다.")
    st.stop()

# ── 목록 ──────────────────────────────────────────────────────
left, right = st.columns([2, 3])

with left:
    st.subheader("세션 목록")
    options = [s["session_id"] for s in sessions]

    def fmt(sid: str) -> str:
        s = next(x for x in sessions if x["session_id"] == sid)
        flags = ""
        if s.get("has_image"): flags += "🖼"
        if s.get("has_voice"): flags += "🎙"
        if s["status"] == "deleted": flags += "🗑"
        return f"{sid}  {flags}  ·  {s['status']}"

    selected = st.radio("세션 선택", options, format_func=fmt, label_visibility="collapsed")

# ── 상세 ──────────────────────────────────────────────────────
with right:
    s = database.get_session(selected)
    if not s:
        st.error("세션을 불러올 수 없습니다.")
        st.stop()

    st.subheader(f"📋 {s['session_id']}")
    st.caption(f"생성: {s['created_at']}  ·  상태: {s['status']}  ·  사별 후 {s.get('bereavement_weeks', '-')}주")
    st.markdown(f"**요약** — {prompt_builder.build_persona_summary(s)}")

    tab_payload, tab_prompt, tab_assets, tab_model, tab_danger = st.tabs(
        ["설문 응답", "AI 페르소나 프롬프트", "자산", "3D 모델", "위험 작업"]
    )

    with tab_payload:
        st.json(s["payload"])
        st.download_button(
            "JSON 다운로드",
            data=json.dumps(s["payload"], ensure_ascii=False, indent=2),
            file_name=f"{s['session_id']}.json",
            mime="application/json",
        )

    with tab_prompt:
        prompt = prompt_builder.build_persona_prompt(s)
        st.code(prompt, language="markdown")
        st.download_button(
            "프롬프트 다운로드",
            data=prompt,
            file_name=f"{s['session_id']}_persona.txt",
            mime="text/plain",
        )

    with tab_assets:
        img = storage.find_image(s["session_id"])
        if img:
            st.image(str(img), caption=img.name, use_container_width=True)
            with img.open("rb") as f:
                st.download_button("이미지 다운로드", f.read(), file_name=img.name)
        else:
            st.info("이미지 없음")

        voice = storage.find_voice(s["session_id"])
        if voice:
            with voice.open("rb") as f:
                voice_bytes = f.read()
            st.audio(voice_bytes)
            st.download_button("음성 다운로드", voice_bytes, file_name=voice.name)
        else:
            st.info("음성 없음")

    with tab_model:
        ms = s.get("model_status", "stub")
        st.markdown(f"**상태**: `{ms}`  ·  업데이트: {s.get('model_updated_at') or '-'}")
        if s.get("meshy_task_id"):
            st.caption(f"Meshy task_id: {s['meshy_task_id']}")
        if s.get("model_error"):
            st.error(s["model_error"])

        # API 키 안내
        client_check = meshy.MeshyClient.from_secrets()
        if client_check.is_stub:
            st.info(
                "현재 **stub 모드**입니다. `.streamlit/secrets.toml`에 `meshy_api_key = \"...\"` 한 줄을 "
                "추가하고 Streamlit을 재시작하면 자동으로 실모드로 전환됩니다."
            )

        # GLB 파일 미리보기 + 다운로드
        glb_path_str = s.get("model_glb_path")
        if glb_path_str:
            from pathlib import Path as _P
            glb_path = _P(glb_path_str)
            if glb_path.exists():
                st.success(f"✓ 모델 파일 보유 ({glb_path.stat().st_size / 1024:.1f} KB)")
                st.code(str(glb_path), language="text")
                with glb_path.open("rb") as f:
                    st.download_button("model.glb 다운로드", f.read(),
                                       file_name=f"{s['session_id']}.glb",
                                       mime="model/gltf-binary")
            else:
                st.warning("DB엔 경로가 있지만 파일이 사라졌습니다.")

        # 재시도 / 디스패치
        if s.get("has_image") and ms in ("stub", "queued", "failed"):
            if st.button("3D 모델 작업 다시 시도", type="primary"):
                result = jobs.retry(s["session_id"])
                st.success(f"디스패치 결과: {result}")
                st.rerun()

    with tab_danger:
        st.warning("아래 작업은 되돌릴 수 없습니다.")
        if s["status"] != "deleted":
            if st.button("이 세션 폐기 (soft delete)", type="primary"):
                storage.purge_assets(s["session_id"])
                database.soft_delete(s["session_id"])
                st.success("폐기 완료")
                st.rerun()
        if st.button("이 세션 영구 삭제 (DB row까지 제거)"):
            storage.purge_assets(s["session_id"])
            database.hard_delete(s["session_id"])
            st.success("영구 삭제 완료")
            st.rerun()
        if s["status"] == "submitted":
            if st.button("'사용됨'으로 표시 (체험 완료 처리)"):
                database.mark_used(s["session_id"])
                st.rerun()
