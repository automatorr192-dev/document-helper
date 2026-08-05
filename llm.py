import os
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()


def _models(env_key: str, default: str) -> list[str]:
    return [m.strip() for m in os.environ.get(env_key, default).split(",") if m.strip()]


MODELS = _models("MODELS", "anthropic/claude-sonnet-5,anthropic/claude-sonnet-4.6")
MODELS_LIGHT = _models("MODELS_LIGHT", "anthropic/claude-haiku-4.5")

# Доллары за миллион токенов (вход, выход) по первой подходящей приставке.
PRICE_USD = {
    "anthropic/claude-opus": (5.0, 25.0),
    "anthropic/claude-sonnet": (3.0, 15.0),
    "anthropic/claude-haiku": (1.0, 5.0),
}
RUB_PER_USD = float(os.environ.get("RUB_PER_USD", 80))


def cost_rub(model: str, input_tokens: int, output_tokens: int) -> float:
    for prefix, (pin, pout) in PRICE_USD.items():
        if model.startswith(prefix):
            usd = (input_tokens * pin + output_tokens * pout) / 1_000_000
            return round(usd * RUB_PER_USD, 2)
    return 0.0


@dataclass
class Reply:
    text: str
    model: str
    input_tokens: int
    output_tokens: int

    @property
    def cost(self) -> float:
        return cost_rub(self.model, self.input_tokens, self.output_tokens)


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
            timeout=120,
        )
    return _client


async def chat(system: str, content, models: list[str], max_tokens: int = 2500) -> Reply:
    client = _get_client()
    last_error = "нет ответа"
    for model in models:
        try:
            resp = await client.chat.completions.create(
                model=model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
            )
            text = resp.choices[0].message.content
            if text:
                usage = resp.usage
                return Reply(
                    text=text.strip(),
                    model=model,
                    input_tokens=usage.prompt_tokens if usage else 0,
                    output_tokens=usage.completion_tokens if usage else 0,
                )
        except Exception as e:
            last_error = str(e)
    raise RuntimeError(last_error)
