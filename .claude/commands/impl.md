---
description: Codex(luna/max)에게 Python 쪽 구현안을 받아 메인이 적용한다. Server/ Web/ Survey/ tools/ 만.
argument-hint: <구현할 것>
allowed-tools: Bash, Read, Grep, Glob, Edit, Write
---

할 일: $ARGUMENTS

## 이 기계에서 Codex 는 쓰기를 못 한다 — 실측

`-s workspace-write` 를 줘도 **`sandbox: read-only` 로 강등된다.** `codex doctor` 가 이유를
말한다: **`sandbox backend disabled`** — Windows 샌드박스 헬퍼가 설치돼 있지 않다.
`--approve-for-me` 도 마찬가지였다.

그래서 **Codex 가 편집안을 내고 메인이 적용한다.**

**diff 를 받지 마라.** 실측했다 — Codex 가 낸 unified diff 는 헝크 헤더가 틀려
`git apply --check` 가 `corrupt patch` 로 거절했다. `--recount` 로도 안 됐다.
**앵커 기반 편집안**(바꿀 원문 그대로 · 바꿀 내용)으로 받아야 깨지지 않는다.

## 절대 규칙 — 경로

**`Assets/` 와 `docs/` 는 편집안 대상에서 뺀다.**

- `Assets/` — Unity 가 파일을 잠그고 `.meta` GUID 충돌은 복구가 어렵다.
  씬은 GUID 참조 목록이라 두 손이 동시에 만지면 참조가 끊긴다
- `docs/` — 이 저장소의 진짜 자산이다. 메인만 쓴다

**할 일이 `Assets/` 를 건드려야 하면 이 커맨드를 쓰지 말고 메인이 직접 한다.**

대상: `Server/` `Web/` `Survey/` `tools/`

## 1. 실행 전

`git status` 로 작업 트리가 깨끗한지 본다. 더러우면 사용자에게 알린다 — 나중에 무엇이
누구 변경인지 못 가린다.

## 2. Codex 에 편집안을 시킨다

`run_in_background: true` 로 띄운다.

```bash
codex exec \
  -m gpt-5.6-luna \
  -c model_reasoning_effort="max" \
  -s read-only \
  --ephemeral \
  -o "C:/Users/user/Documents/GitHub/Capstone-Design/.claude/work/impl-$(date +%H%M%S).md" \
  "<프롬프트>" < /dev/null
```

**`< /dev/null` 을 빠뜨리지 마라.** `codex exec` 는 stdin 이 열려 있으면 EOF 를 기다리며
멈춘다 — 13분이 지나도 안 끝난다(실측). 붙이면 같은 프롬프트가 5.3초에 끝난다.

프롬프트에 반드시 넣을 것:

- 할 일
- **"파일을 쓰지 마라. 편집안만 내라"**
- **출력 형식을 못박는다** — 편집 하나당 이 세 줄:
  ```
  파일: <경로>
  찾을 원문: <파일에 있는 그대로. 유일하게 식별되도록 충분히 길게>
  바꿀 내용: <새 내용>
  ```
- **"diff 나 patch 형식으로 내지 마라"**
- 먼저 읽을 문서 (`Server/README.md` / `Web/README.md`)
- **"`Assets/` 와 `docs/` 는 건드리지 마라"**
- 이 저장소 양식 — 주석과 문서는 **한국어 서술형**이다
- **"추측성 기능·설정·추상화를 넣지 마라. 시킨 것만 해라"**

## 3. 메인이 적용한다

1. `-o` 파일을 읽는다 (`.log` 전체는 읽지 않는다)
2. **편집안을 그대로 믿지 말고 각 앵커가 실제 파일에 있는지 확인한다.** Codex 는 이
   저장소를 읽었지만 원문을 옮겨 적다 틀릴 수 있다
3. `Edit` 로 하나씩 적용한다
4. `git diff` 로 결과를 확인한다. **`Assets/` 나 `docs/` 가 걸리면 되돌린다**
5. `Server/` 를 고쳤으면 실 서버(`~/server`)와 어긋난다는 것을 사용자에게 알린다

## Codex 에게 직접 쓰게 하려면

둘 중 하나를 사용자가 골라야 한다. **내가 임의로 켜지 않는다.**

| 길 | 무엇 | 대가 |
|---|---|---|
| Windows 샌드박스 설치 | `codex doctor` 가 시키는 Defender 예외 추가 | 관리자 권한 필요 |
| `--dangerously-bypass-approvals-and-sandbox` | 샌드박스를 통째로 끈다 | **어떤 명령이든 무제한 실행.** 이 저장소에는 참여자 사진·음성이 있다 |
