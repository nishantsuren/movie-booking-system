"""Articulation: a second, separate LLM call that rephrases the
deterministic text dialogue_manager already decided on, never the
source of any fact in it. dialogue_manager/templates remain the only
place that decides *what* to say -- this module only changes *how* it
sounds. Fails closed to the original text on any Ollama error, timeout,
or empty/malformed output, so a flaky model can never block a turn or
replace a real answer with nothing.
"""
import httpx

from config import ARTICULATION_TEMPERATURE, ARTICULATION_TIMEOUT_SECONDS, OLLAMA_MODEL, OLLAMA_URL

_PROMPT_TEMPLATE = """Rephrase the following so it sounds natural and conversational. Keep every name, number, and fact exactly as given. Do not add, remove, or invent any fact. Reply with only the rephrased message, nothing else.

Message: {template_text}
Rephrased:"""


def articulate(template_text: str) -> str:
    prompt = _PROMPT_TEMPLATE.format(template_text=template_text)

    try:
        resp = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": ARTICULATION_TEMPERATURE},
            },
            timeout=ARTICULATION_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        rephrased = resp.json()["response"].strip()
    except (httpx.HTTPError, KeyError, ValueError):
        return template_text

    # The model sometimes wraps its whole reply in a matching pair of
    # quotes despite the prompt not asking for any -- strip exactly one
    # such pair, never more, so a quote that's actually part of the
    # rephrased text is left alone.
    if len(rephrased) >= 2 and rephrased[0] == rephrased[-1] and rephrased[0] in "\"'":
        rephrased = rephrased[1:-1].strip()

    return rephrased or template_text
