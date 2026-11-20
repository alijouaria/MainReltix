"""LLM text generation with canned fallbacks; never raises."""
import os

from dotenv import load_dotenv

from app import fallbacks

load_dotenv()

KINDS = (
    "alert_explanation", "tenant_checkin", "landlord_approval",
    "tenant_notice", "call_script", "case_summary",
)
TIMEOUT_S = 8.0

SYSTEM = (
    "You are Guardian, a heating and energy assistant working for reltix property management. "
    "Write short, plain, friendly English. No markdown. Use only facts from the context."
)


def _call_openai(kind: str, context: dict) -> str:
    from openai import OpenAI

    client = OpenAI(timeout=TIMEOUT_S, max_retries=0)
    resp = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Write a {kind.replace('_', ' ')}. Context: {context}"},
        ],
    )
    text = (resp.choices[0].message.content or "").strip()
    if not text:
        raise ValueError("empty completion")
    return text


class _Blank(dict):
    def __missing__(self, key):
        return ""


GENERIC = "Guardian update from reltix property management."


def _fallback(kind: str, context: dict) -> str:
    try:
        return fallbacks.TEXT[kind].format_map(_Blank(context or {}))
    except Exception:
        return GENERIC


def generate(kind: str, context: dict) -> str:
    """Never raises: OpenAI if keyed and available, else canned fallback."""
    if kind not in fallbacks.TEXT or not os.environ.get("OPENAI_API_KEY"):
        return _fallback(kind, context)
    try:
        return _call_openai(kind, context)
    except Exception:
        return _fallback(kind, context)
