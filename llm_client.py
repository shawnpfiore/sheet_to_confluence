#!/usr/bin/env python3
import os
from typing import List, Dict

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---- Env / defaults ----

# Which backend to use. For now we only support "ollama".
LLM_BACKEND = os.getenv("LLM_BACKEND", "ollama").lower()

# Native Ollama HTTP endpoint #need to update using for MCP
# For Hive:
#   https://thehive.tib.ad.ea.com/api/generate
# For local:
#   http://localhost:11434/api/generate
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://localhost:11434/api/generate")

# Model tag to use with Ollama
LLM_MODEL_TAG = os.getenv("LLM_MODEL_TAG", os.getenv("OLLAMA_MODEL", "codellama:7b"))


def _call_ollama_native(prompt: str) -> str:
    payload = {
        "model": LLM_MODEL_TAG,
        "prompt": prompt,
        "stream": False,
    }

    resp = requests.post(
        OLLAMA_API_URL,
        json=payload,
        verify=False,   # internal / self-signed
        timeout=480,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"Ollama error {resp.status_code}: {resp.text}")

    data = resp.json()
    return data.get("response", "")


def call_llm_with_messages(messages: List[Dict[str, str]]) -> str:
    """
    messages: list of { "role": "system" | "user", "content": "..." }

    For Stage 1 we only support LLM_BACKEND="ollama" using the native
    /api/generate endpoint by concatenating system + user messages into one prompt.
    """
    if LLM_BACKEND != "ollama":
        raise RuntimeError(f"Only LLM_BACKEND=ollama is supported in Stage 1 (got {LLM_BACKEND})")

    prompt_parts = [m["content"] for m in messages if m["role"] in ("system", "user")]
    prompt = "\n\n".join(prompt_parts).strip()

    return _call_ollama_native(prompt)
