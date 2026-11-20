"""
Deterministic stand-ins for the LLM client, so the test suite can run
fully offline without a real Ollama/OpenAI-compatible server.
"""
import math

VOCAB = ["e24", "e15", "drain", "pump", "leak", "kinked", "filter", "water", "dishwasher", "service"]


def fake_embed_texts(texts: list[str]) -> list[list[float]]:
    vectors = []
    for text in texts:
        lower = text.lower()
        vec = [lower.count(word) for word in VOCAB]
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        vectors.append([v / norm for v in vec])
    return vectors


def fake_chat_e24(system_prompt: str, user_prompt: str, temperature: float = 0.1) -> str:
    """Simulates an LLM that actually reads the retrieved excerpts: answers
    about E24 if E24 text is present in the prompt, otherwise says not found."""
    if "e24" in user_prompt.lower() and "drain pump is blocked" in user_prompt.lower():
        return "Error E24 means the drain pump is blocked or the drain hose is kinked."
    from app.services.query_service import NOT_FOUND_PHRASE

    return NOT_FOUND_PHRASE


def fake_detect_chat(system_prompt: str, user_prompt: str, temperature: float = 0.1) -> str:
    return (
        '{"name": "Bosch Dishwasher", "manufacturer": "Bosch", '
        '"model_number": "SHPM88Z75N", "device_type": "Dishwasher"}'
    )
