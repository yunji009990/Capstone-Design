# CLAUDE.md — 다시, 봄

고인의 사진·음성으로 3D 인물과 목소리를 복원해 VR에서 대화하는 캡스톤.
**Unity(VR) + Raon 음성 서버 + 웹 등록 도구** 세 덩어리가 한 저장소에 있다.

개발선은 **`jw`** 다. `main` 은 186 커밋 뒤져 있으니 기준으로 삼지 말 것.

---

## 1. 손대기 전에 읽을 것

`docs/` 에 220KB 가 쌓여 있다. **거기 이미 답이 있는 것을 다시 파는 것이 이 프로젝트의
가장 큰 시간 낭비였다.** 무엇을 하려는지에 따라 먼저 읽을 문서가 정해져 있다.

| 하려는 일 | 먼저 읽을 것 |
|---|---|
| 아무거나 — 현재 상태·함정·다음 할 일 | `docs/음성대화_작업현황.md` §4(함정) · §6(미해결) |
| 모델 쪽을 건드린다 | `docs/Raon모델_분석.md` — **필수** |
| 인물·말투·프롬프트를 만든다 | `docs/페르소나_작성규격.md` |
| 참조 음성 가설을 세운다 | `docs/참조음성_실험결과.md` (15조건 75파일 원자료) |
| 전이중을 시도한다 | `docs/전이중_조사.md` — **시도 전에** |
| 서버를 복구·재배포한다 | `Server/README.md` |
| 웹 등록 흐름을 고친다 | `Web/README.md` |

## 2. 이미 기각된 것 — 다시 파지 말 것

전부 실측으로 닫혔다. 다시 열려면 **먼저 위 문서에서 왜 닫혔는지 읽고**, 그때와 무엇이
달라졌는지 말할 수 있어야 한다.

- **지지직(고주파 아티팩트)** — 여덟 가지 기각됨. 저역통과·잡음제거·온도·top_k 다시 하지 말 것.
  기준은 **1~4kHz 비율 25% 미만**이다. 저역 30% 기준은 틀렸고 교체됐다
- **SNR·참조 길이** — 지지직과 무관함이 실측됨. 35dB 가 통과하며 실패했고 59dB 도 그대로였다
- **참조 길이·말투** — 세 회차로 기각. TitaNet 도 무음프레임도 다시 재지 말 것
- **전이중(full-duplex)** — 지금 모델로 불가. 21B 는 인코더가 비인과, 9B 는 영어 전용
- **요약(`RAON_SUMMARY`)** — 켜면 기억이 62/64 → 50/64 로 무너진다
- **`torch.compile(mode="reduce-overhead")`** — 세그폴트로 서버가 죽는다

## 3. 어디를 고치는가

**줄 번호는 쓰지 않는다** — 예전 문서가 네 함수의 줄 번호를 적어 뒀다가 코드가 14줄 밀린
뒤로 넷 다 틀린 채 남아 있었다. 함수 이름으로 찾을 것.

| 바꾸려는 것 | 어디 |
|---|---|
| 말투·성격·호칭·대화 예시 | `Web/persona.py` `build_persona()` |
| 사전지식에 들어갈 사실 | `Web/persona.py` `build_knowledge()` |
| 설문 문항 | `Web/static/index.html` + 위 둘 |
| 공통 규칙 (길이·기호·반말) | `Server/app.py` `BASE_RULES` |
| 답을 뽑는 방식 | `Server/app.py` `answer_for()` |
| 프롬프트 조립 순서 | `Server/app.py` `build_msgs()` |
| 판정기 | `Server/app.py` `unknown()`, `JUDGE_PROMPT`, `BACKREF` |
| 적립 | `Server/app.py` `learn()`, `LEARN_PROMPT` |

### 서버는 저장소가 둘이다

`Server/` 는 실 서버(`crc_unity@220.69.208.201:~/server`)의 **사본**이다. 접속은
`ssh raon` (키 `~/.ssh/capstone-auto`).

서버의 `~/server` 에도 **독립 git 저장소**가 있다. 리모트가 없어 그 기계에만 있고,
이 저장소와 이어주는 것은 아무것도 없다 — `scp` 로 사람이 옮기고 해시로 대조하는
수밖에 없다. 실제로 2026-09-04 에 대조했더니 `modeling_raon.patch` 가 어긋나 있었다.

**배포 절차**

```bash
# 1. 저장소를 먼저 고치고 커밋 (되돌릴 지점)
# 2. 올린다
scp Server/app.py raon:~/server/app.py
# 3. 정지와 기동은 따로. 묶지 않는다
ssh raon "cd ~/server; ./stop.sh"
ssh raon "cd ~/server; ./start.sh"
# 4. 20초 뒤 확인. 로그를 안 보면 배포한 게 아니다
curl http://220.69.208.201:8000/health
# 5. 서버에서도 커밋한다 — 안 하면 되돌릴 지점이 안 생긴다
ssh raon "git -C ~/server commit -a -m '...'"
```

- **어긋났는지 보려면 해시로 대조한다** — `ssh raon "md5sum ~/server/app.py"`
- **`sessions/` 는 절대 커밋하지 않는다** — 등록된 실존 인물의 음성·사진이다
- **`rm` 금지, `~/models` 는 읽기만** (45GB, 백업 없음)
- 재시작하면 **20초간 대화가 끊긴다.** 시연 중에는 하지 않는다. 등록된 인물은 살아남는다

## 4. 이 프로젝트의 하네스

**메인(나) 하나 + 서브 셋을 쓴다.** 아래 역할대로만 쓰고, 그 밖에는 메인 단독으로 한다.

| | 무엇 | 어떻게 부르나 |
|---|---|---|
| 메인 | Claude Code · Opus 5 · effort max. 계획 · 통합 · 최종 판단 | — |
| 서브① | 문서 대조 — "이거 이미 기각됐나" | `Agent` 툴, `subagent_type: "doc-scout"` |
| 서브② | 독립 진단 · 반론 (Codex luna/max, 읽기 전용) | `/verify` |
| 서브③ | Python 쪽 구현안 (Codex luna/max, 읽기 전용 — **적용은 메인이**) | `/impl` |

**Agent 툴과 `/verify` · `/impl` 사용을 이 문서로 허가한다.** 위 표의 상황에서는 물어보지
말고 부른다.

### 쓰기 권한 — 어기면 되돌리기 어렵다

| 경로 | 쓰는 쪽 |
|---|---|
| `Assets/` | **메인만.** Unity 가 파일을 잠그고 `.meta` GUID 충돌은 복구가 어렵다. 씬은 GUID 참조 목록이라 두 손이 동시에 만지면 참조가 끊긴다 |
| `docs/` | **메인만.** 이 저장소의 진짜 자산이다 |
| `Server/` `Web/` `Survey/` `tools/` | 메인. 서브③ 의 편집안을 받아 적용한다 |
| 전부 | **서브 셋 다 읽기만.** 이 기계에서 Codex 는 쓰기를 못 한다(아래) |

**Unity 작업에는 서브를 쓰지 않는다.** git worktree 도 쓰지 않는다 — `Assets/Models/`
848MB 라 worktree 하나가 900MB 고 Unity 가 Library 를 다시 굽는다.

### 이 기계의 Codex 는 쓰기를 못 한다 — 실측

`-s workspace-write` 를 줘도 **`sandbox: read-only` 로 강등된다.** `--approve-for-me` 도
같았다. `codex doctor` 가 이유를 말한다: **`sandbox backend disabled`** — Windows 샌드박스
헬퍼가 설치돼 있지 않다.

**diff 를 주고받지도 마라.** Codex 가 낸 unified diff 는 헝크 헤더가 틀려
`git apply --check` 가 `corrupt patch` 로 거절했다(`--recount` 로도 실패).
**앵커 기반 편집안**(찾을 원문 · 바꿀 내용)으로 받아 메인이 `Edit` 로 적용한다.

쓰기를 뚫으려면 Windows 샌드박스를 설치하거나 샌드박스를 통째로 꺼야 하는데,
**둘 다 사용자가 고를 일이다. 임의로 켜지 않는다.**

### Codex 를 부를 때 stdin 을 끊어야 한다

`codex exec` 는 **stdin 이 열려 있으면 EOF 를 기다리며 멈춘다.** 실측으로 확인했다 —
같은 프롬프트가 stdin 을 안 끊으면 13분이 지나도 안 끝나고(CPU 2.7초, 계산이 아니라 대기),
`< /dev/null` 을 붙이면 **5.3초**에 끝난다.

**모든 `codex exec` 호출 끝에 `< /dev/null` 을 붙인다.** 빠뜨리면 조용히 매달린다.

### 서브를 부르지 않는 때

기본은 **메인 단독**이다. 단순 질문·수정·탐색은 혼자 한다. 서브는 §4 표의 상황에서만 부른다.

## 5. 셸 경로 규칙 — 무인 실행을 멈추게 하는 것

**`cd` 뒤에 상대경로를 쓰지 마라.** 권한 검사기는 `cd` 이후의 상대경로가 실제로 어디를
가리키는지 **정적으로 확정하지 못한다.** 이 저장소에는 `deny` 규칙(`Web/.env`,
`Survey/data/**`)이 있어서, 확정 못 하면 "혹시 deny 영역일 수도" 로 보고 사람 승인을
요구한다. `bypassPermissions` 여도 뜬다.

실제로 이 창이 떴다: `git on 'Server/app.py' after a cd would search a directory that
cannot be determined here, and a Read() deny rule is configured`

무인으로 오래 돌릴 때 이 이유로 멈춘다. **권한을 더 푸는 것이 아니라 이 패턴을 안 쓰는
것이 해결책이다.**

### git — `-C` 를 쓴다

```bash
# 나쁨
cd "C:/Users/user/Documents/GitHub/Capstone-Design" && git diff --stat Server/app.py

# 좋음
git -C "C:/Users/user/Documents/GitHub/Capstone-Design" diff --stat -- Server/app.py
```

`--` 로 경로임을 못박는 것까지 같이 한다.

### 그 밖

- 작업 디렉터리를 받는 옵션이 있으면 그것을 쓴다 (`codex exec -C`, `scp` 절대경로 등)
- 파이썬·스크립트 안에서 파일을 열 때는 **절대경로 문자열**을 쓴다
  ```python
  p = r"C:/Users/user/Documents/GitHub/Capstone-Design/Server/app.py"
  ```
- 넘겨줄 출력 경로(`-o`, `>` 등)도 절대경로로 쓴다
- 전용 툴(Read/Edit/Write)은 원래 절대경로를 받으므로 이 문제가 없다

## 6. 이 프로젝트에서 자주 나는 사고

- **단정하기 전에 계측기부터 검증한다.** 2026-08-01 하루에 잘못된 원인 진단을 일곱 번
  했다. 코드가 아니라 판단에서 시간을 다 썼다. 비교는 **한 번에 한 변수**, 오디오는
  **소스 3개 · 회차 3회** 이상. 확신이 안 서면 "미확정"으로 남긴다
- **오디오 판정은 사용자의 귀로 한다.** 나는 도구와 숫자까지만. 들을 파일은 `들어볼것\` 에 모은다
- **서버 명령은 한 번에 하나씩.** 결과를 받고 다음을 준다. `&&` 대신 `;`.
  `./start.sh` 를 `tail -f` 같은 포그라운드 명령과 `&&` 로 묶지 말 것 — Ctrl-C 가 서버까지 죽인다
- **병합은 마지막에 한다.** 다 만들고 나서 병합한다. 다음 할 일로 제안하지 말 것
- **윈도우 자식 프로세스는 콘솔이 아니라 로케일(cp949)로 인코딩한다.** 파이썬을 부를 때
  한글이 깨지면 `sys.stdout.reconfigure(encoding='utf-8')` 또는 `PYTHONUTF8=1`
- **평가는 반드시 대화부터 잰다.** `--probe` 는 물음을 하나씩 떼어 묻기 때문에 대화
  안에서만 나는 결함을 못 본다 — 실제로 세 번 놓쳤다. `/eval` 참고
