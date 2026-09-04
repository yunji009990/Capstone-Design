---
description: 프롬프트 평가를 절차대로 돌린다. 대화를 먼저 재고 그다음 probe.
argument-hint: <변형 이름>
allowed-tools: Bash, Read, Glob
---

변형: $ARGUMENTS

## 순서를 어기지 말 것

**반드시 대화부터 잰다.** `--probe` 는 물음을 하나씩 떼어 묻기 때문에 **대화 안에서만 나는
결함을 못 본다.** 2026-08-28 에 판정기와 적립을 붙이며 세 번 망가뜨렸고 **셋 다 probe
로는 안 보였다** — 앵무새(기억 1/8), 이어 묻는 말 놓침, 판정기가 아는 것까지 막기(기억 52%).

probe 숫자만 보면 셋 다 좋아 보인다. 기억이 무너지면 지어내기를 아무리 잡아도 대화 자체가 안 된다.

## 1단계 — 대화 (20턴)

```bash
RAON_EVAL_VOICE=<참조.wav> python "C:/Users/user/Documents/GitHub/Capstone-Design/tools/prompt_eval.py" $ARGUMENTS -n 4
```

## 2단계 — 지어내기 (1단계가 정상일 때만)

```bash
RAON_EVAL_VOICE=<참조.wav> python "C:/Users/user/Documents/GitHub/Capstone-Design/tools/prompt_eval.py" $ARGUMENTS --probe -n 6
```

## 읽는 법

- **최소 4회차.** 같은 설정을 다섯 번 돌렸더니 4, 4, 8, 17, 6 이 나왔다. 단발로 결론 내지 말 것
- **소리는 안 잰다.** 지지직·억양·속도는 사용자의 귀로 판정한다. 들을 파일은 `들어볼것\` 에 모은다
- 변형 파일은 `C:/Users/user/Documents/GitHub/Capstone-Design/tools/prompts/<이름>.{persona,knowledge,rules}.md` 셋이다

## 끝나고 반드시 알릴 것

**서버의 등록 인물이 `eval` 로 바뀐다.** 시연 전에 웹에서 다시 등록해야 한다는 것을
사용자에게 알린다.

참조 음성은 실존 인물 목소리라 저장소에 없다. **글로만 잴 때는 아무 wav 나 된다** —
`/chat` 은 합성을 안 하므로 잡음 12초짜리면 등록 형식을 통과한다.
