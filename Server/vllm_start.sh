#!/usr/bin/env bash
# 답변 LLM 서버 (포트 8001). Raon(8000)과 같은 카드에 얹는다.
#
# **이 기계는 공용이다.** uc 사용자의 vr_voice_server.py 가 6.2GB 를 상시 쓴다.
# 실제 가용은 95GB 가 아니라 약 89GB 다. Raon 이 43~46GB 를 쥐므로 여기는 46GB 안이다.
#
# 함정 다섯 (전부 실측으로 겪음)
#  1) --limit-mm-per-prompt 0 : EXAONE 4.5 는 비전 모델이라 뜰 때 비디오 경로를
#     프로파일링하다 죽는다 (no attribute 'input_norm'). 우리는 글만 쓴다.
#  2) VLLM_USE_FLASHINFER_SAMPLER=0 : 'FlashInfer requires GPUs with sm75 or higher'
#     로 죽는다. 이 카드는 sm120(Blackwell)이라 조건은 넘는데 JIT 검사가 못 알아본다.
#     어텐션이 아니라 **샘플러**다 (v1/sample/ops/topk_topp_sampler.py).
#  3) EXTRA=--enforce-eager : CUDA 그래프 캡처가 몇 GB 를 더 문다. 빠듯하면 끈다.
#  4) 27B FP8(29GB)은 안 들어간다. 4비트(23GB)까지가 한계다.
#  5) --max-num-seqs : Qwen3.5 는 Mamba 혼합 구조라 기본 1024 슬롯이면
#     'max_num_seqs (1024) exceeds available Mamba cache blocks' 로 죽는다.
#     우리는 한 번에 한 요청만 쓰므로 줄여도 손해가 없다.
#
# 쓰는 법
#   MODEL=/home/crc_unity/models/gemma-4-31B-qat nohup ./vllm_start.sh > vllm.log 2>&1 &
MODEL=${MODEL:-/home/crc_unity/models/gemma-4-31B-qat}
export VLLM_ATTENTION_BACKEND=${ATTN:-TRITON_ATTN}
export VLLM_USE_FLASHINFER_SAMPLER=0
exec /home/crc_unity/venv/vllm/bin/vllm serve "$MODEL" \
  --served-model-name exaone \
  --host 127.0.0.1 --port 8001 \
  --gpu-memory-utilization "${UTIL:-0.42}" \
  --max-model-len "${MAXLEN:-8192}" \
  --max-num-seqs "${SEQS:-32}" \
  --limit-mm-per-prompt "${MM:-{\"image\":0,\"video\":0\}}" \
  --trust-remote-code ${EXTRA:-}
