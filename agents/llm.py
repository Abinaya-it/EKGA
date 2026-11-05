"""Thin client for a local OpenAI-compatible server (Ollama / vLLM / llama.cpp). Returns None when no LLM is configured,
so every caller has a deterministic fallback and the whole system runs (and is evaluated) without a model."""
import requests
from config import LLM_URL, LLM_MODEL


def available(): return bool(LLM_URL)


def chat(messages, json_mode=False, max_tokens=512, temperature=0.0, timeout=60):
    if not LLM_URL: return None
    body = {"model": LLM_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
    if json_mode: body["response_format"] = {"type": "json_object"}
    try:
        r = requests.post(f"{LLM_URL}/chat/completions", json=body, timeout=timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        return None
