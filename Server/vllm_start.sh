#!/usr/bin/env bash
# Gemma answer model, port 8001. Shared GPU; existing allocation is retained.
# The served name exaone is an API compatibility alias, not the loaded model.
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
