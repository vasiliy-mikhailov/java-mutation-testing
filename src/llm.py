"""Qwen FP8 client — the one reused inference dependency.

Gateway: OpenAI-compatible vLLM at QWEN_BASE_URL (…/qwen-3.6-27b-fp8/v1), model qwen-3.6-27b-fp8.
Server runs --reasoning-parser qwen3, so chain-of-thought lands in message.reasoning and the
returned `content` is clean. It's a THINKING model: thinking can eat the whole token budget and
return empty content with finish_reason=length, so callers pass a generous max_tokens cap.
Key is read at runtime from .env (OC_KEY or QWEN_API_KEY) — never hardcoded (shared, rotating).
"""
import re, json
from openai import OpenAI
from common import env

MODEL = env("QWEN_MODEL", "qwen-3.6-27b-fp8")
_KEY = env("OC_KEY") or env("QWEN_API_KEY")
_BASE = env("QWEN_BASE_URL") or env("OC_BASE_URL")
_client = OpenAI(api_key=_KEY, base_url=_BASE)

_THINK = re.compile(r"<think>.*?</think>", re.S)  # belt-and-braces if a raw think ever leaks


def complete(messages, temperature=0.0, max_tokens=24000, thinking_budget=None):
    extra = {}
    if thinking_budget is not None:
        extra["thinking_token_budget"] = thinking_budget  # honored only if the runtime supports it
    r = _client.chat.completions.create(
        model=MODEL, messages=messages,
        temperature=temperature, max_tokens=max_tokens,
        extra_body=extra or None)
    msg = r.choices[0].message
    txt = msg.content or ""
    if not txt.strip():
        # truncated mid-think: surface finish_reason so callers can log it
        raise RuntimeError(f"empty content (finish_reason={r.choices[0].finish_reason})")
    return _THINK.sub("", txt).strip()


def extract_json(text):
    """Pull the first JSON object out of a response (prefers a ```json fence)."""
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    blob = m.group(1) if m else None
    if blob is None:
        i, j = text.find("{"), text.rfind("}")
        blob = text[i:j + 1] if i >= 0 and j > i else text
    return json.loads(blob)
