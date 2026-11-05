"""DeepSeek chat-completions adapter. No provider fallback is performed."""

import asyncio
import json
import os
from urllib.parse import urlparse

import httpx

from protocol.models import ChatMessage, ModelResponse, ToolCall, ToolDefinition


class ModelCallError(RuntimeError):
    pass


class DeepSeekClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_sec: float = 45,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.deepseek.com"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") not in {"", "/v1"}
            or parsed.port not in {None, 443}
        ):
            raise ValueError("this adapter requires the official DeepSeek HTTPS endpoint")
        if not api_key or not model:
            raise ValueError("LLM_API_KEY and LLM_MODEL are required")
        if not 0 <= max_retries <= 5 or timeout_sec <= 0:
            raise ValueError("invalid model retry or timeout configuration")
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries
        self.http = httpx.AsyncClient(timeout=timeout_sec, transport=transport)

    @classmethod
    def from_environment(cls) -> "DeepSeekClient":
        names = ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
        if any(not os.environ.get(name) for name in names):
            raise ValueError("LLM_BASE_URL, LLM_API_KEY and LLM_MODEL must be set")
        return cls(
            base_url=os.environ["LLM_BASE_URL"],
            api_key=os.environ["LLM_API_KEY"],
            model=os.environ["LLM_MODEL"],
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def complete(
        self, messages: list[ChatMessage], tools: list[ToolDefinition], *, max_tokens: int
    ) -> ModelResponse:
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        wire_messages = []
        for message in messages:
            wire: dict = {"role": message.role, "content": message.content}
            if message.tool_call_id:
                wire["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                wire["tool_calls"] = [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in message.tool_calls
                ]
            wire_messages.append(wire)
        payload: dict = {
            "model": self.model,
            "messages": wire_messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in tools
            ]
        for attempt in range(self.max_retries + 1):
            try:
                response = await self.http.post(
                    self.url, headers={"Authorization": f"Bearer {self.api_key}"}, json=payload
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == self.max_retries:
                    raise ModelCallError("Model request failed after bounded retries") from exc
            else:
                if response.status_code == 200:
                    try:
                        body = response.json()
                        choice = body["choices"][0]
                        if choice.get("finish_reason") == "length":
                            raise ModelCallError("Model output exceeded the response limit")
                        message = choice["message"]
                        return ModelResponse(
                            content=message.get("content") or "",
                            tool_calls=[
                                ToolCall(
                                    call_id=call["id"],
                                    name=call["function"]["name"],
                                    arguments=json.loads(call["function"]["arguments"]),
                                )
                                for call in message.get("tool_calls") or []
                            ],
                            tokens=body.get("usage", {}).get("total_tokens", 0),
                        )
                    except (KeyError, IndexError, TypeError, ValueError) as exc:
                        raise ModelCallError("Model returned an invalid response envelope") from exc
                if response.status_code != 429 and response.status_code < 500:
                    raise ModelCallError(f"Model request returned HTTP {response.status_code}")
                if attempt == self.max_retries:
                    raise ModelCallError("Model service unavailable after bounded retries")
            await asyncio.sleep(min(0.25 * 2**attempt, 2))
        raise ModelCallError("Model request could not complete")
