"""DeepSeek Chat Completions adapter."""

from dataclasses import dataclass
from time import perf_counter

from openai import APIConnectionError, APIError, APIStatusError, OpenAI, omit
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from openai.types.shared_params import ResponseFormatJSONObject


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    latency_ms: int


class ProviderError(RuntimeError):
    """A provider failure with a safe message and an outer-retry hint."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class DeepSeekClient:
    def __init__(self, api_key: str, model: str = "deepseek-flash") -> None:
        self.model = model
        self._client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
            timeout=60.0,
            max_retries=0,
        )

    def complete(self, system: str, user: str, *, json_mode: bool = False) -> Completion:
        """Request one completion. With JSON mode, the prompt must describe the JSON output."""
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        response_format: ResponseFormatJSONObject = {"type": "json_object"}
        started = perf_counter()
        try:
            response: ChatCompletion = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=0,
                max_tokens=2048,
                extra_body={"thinking": {"type": "disabled"}},
                response_format=response_format if json_mode else omit,
            )
        except APIStatusError as error:
            status = error.status_code
            raise ProviderError(
                f"DeepSeek {type(error).__name__} (HTTP {status})",
                retryable=status in {408, 409, 429} or 500 <= status < 600,
            ) from None
        except APIConnectionError as error:
            raise ProviderError(f"DeepSeek {type(error).__name__}", retryable=True) from None
        except APIError as error:
            raise ProviderError(f"DeepSeek {type(error).__name__}", retryable=False) from None

        latency_ms = round((perf_counter() - started) * 1000)
        if not response.choices:
            raise ProviderError("DeepSeek returned no completion choices", retryable=True)

        choice = response.choices[0]
        reason = choice.finish_reason
        if reason == "length":
            raise ProviderError("DeepSeek completion was truncated (length)", retryable=False)
        if reason in {"aborted", "insufficient_system_resource"}:
            raise ProviderError(f"DeepSeek completion was interrupted ({reason})", retryable=True)
        if reason == "content_filter":
            raise ProviderError("DeepSeek completion was filtered (content_filter)", retryable=False)
        if reason != "stop":
            raise ProviderError("DeepSeek returned an unexpected finish reason", retryable=False)

        content = choice.message.content
        if not content or not content.strip():
            raise ProviderError("DeepSeek returned a blank completion", retryable=True)

        usage = response.usage
        return Completion(
            text=content.strip(),
            model=response.model or self.model,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            latency_ms=latency_ms,
        )
