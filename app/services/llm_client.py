"""
Thin wrapper around an OpenAI-compatible client.

Both Ollama (via its /v1 endpoint) and most local/self-hosted model
servers speak the OpenAI HTTP API, so a single client covers all of them.
No model is hard-coded -- base URL, API key, and model name all come from
the settings service (configurable from the Settings page).
"""
from openai import OpenAI

from app.services import settings_service


class LLMError(RuntimeError):
    """Raised when the configured LLM/embedding server can't be reached
    or returns an unusable response. Callers should catch this and show
    a friendly message instead of a stack trace."""


def _client(base_url: str, api_key: str) -> OpenAI:
    # Ollama and many local servers don't check the API key, but the
    # OpenAI SDK requires a non-empty string.
    return OpenAI(base_url=base_url, api_key=api_key or "not-needed")


def get_chat_client() -> tuple[OpenAI, str]:
    s = settings_service.get_all()
    client = _client(s["llm_base_url"], s["llm_api_key"])
    return client, s["llm_model"]


def get_embedding_client() -> tuple[OpenAI, str]:
    s = settings_service.get_all()
    client = _client(s["embedding_base_url"], s["llm_api_key"])
    return client, s["embedding_model"]


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    client, model = get_embedding_client()
    try:
        resp = client.embeddings.create(model=model, input=texts)
    except Exception as exc:  # noqa: BLE001 - surface as a friendly error
        raise LLMError(
            f"Could not reach the embedding model '{model}'. "
            f"Check your Settings page and confirm the server is running. ({exc})"
        ) from exc
    return [item.embedding for item in resp.data]


def chat(system_prompt: str, user_prompt: str, temperature: float = 0.1, messages: list[dict] | None = None) -> str:
    """One chat completion. By default a single system+user pair; callers
    running a conversation pass an explicit `messages` list (each entry a
    {"role", "content"} dict) -- same OpenAI-compatible shape, so any
    server Kayock already talks to works unchanged."""
    client, model = get_chat_client()
    if not messages:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=messages,
        )
    except Exception as exc:  # noqa: BLE001
        raise LLMError(
            f"Could not reach the language model '{model}'. "
            f"Check your Settings page and confirm the server is running. ({exc})"
        ) from exc
    return (resp.choices[0].message.content or "").strip()
