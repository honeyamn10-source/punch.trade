"""OpenAI-compatible proxy -> Ollama native API.

Why this exists: qwen3.5:9b is a thinking model. Ollama's own
/v1/chat/completions wrapper silently drops the `think:false` flag, so
the model burns its entire token budget on internal thinking and the
client (e.g. opencode) receives empty content. The native /api/chat
endpoint honours top-level `think:false` and answers instantly.

This router exposes OpenAI-shaped /v1/models and /v1/chat/completions
(stream + non-stream) on the punch.trade server and forwards to Ollama
with thinking disabled. Point any OpenAI-compatible client at
http://127.0.0.1:8000/v1 and it just works.

Security: the /v1 path is intentionally unauthenticated (local AI
client) — only bind it to loopback unless you know what you're doing.
"""

from __future__ import annotations

import json
import time
import uuid

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from .config import HOST

OLLAMA_NATIVE = f"http://{HOST}:11434/api/chat"
OLLAMA_OPENAI_MODELS = f"http://{HOST}:11434/v1/models"

router = APIRouter(prefix="/v1")


def _messages_from_openai(body: dict) -> list[dict]:
    """Convert OpenAI messages to Ollama native format, dropping tool
    messages' function-call noise if the upstream rejects them."""
    out = []
    for m in body.get("messages", []):
        role = m.get("role", "user")
        content = m.get("content")
        if isinstance(content, list):  # multimodal array -> join text parts
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
            content = "\n".join(parts) if parts else ""
        out.append({"role": role, "content": content or ""})
    return out


@router.get("/models")
async def list_models() -> JSONResponse:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(OLLAMA_OPENAI_MODELS)
        return JSONResponse(resp.json())


@router.post("/chat/completions")
async def chat_completions(request: Request) -> Response:
    body = await request.json()
    stream = body.get("stream", False)
    payload = {
        "model": body.get("model", "qwen3.5:9b"),
        "messages": _messages_from_openai(body),
        "stream": stream,
        "think": False,  # the whole point of this proxy
        "options": {
            "num_predict": body.get("max_tokens", 2048),
            "temperature": body.get("temperature", 0.7),
            "top_p": body.get("top_p", 0.95),
        },
    }

    async with httpx.AsyncClient(timeout=None) as client:
        if not stream:
            resp = await client.post(OLLAMA_NATIVE, json=payload)
            data = resp.json()
            usage = (data.get("prompt_eval_count", 0), data.get("eval_count", 0))
            return JSONResponse(
                {
                    "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": data.get("model", body.get("model")),
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": data.get("message", {}).get("content", ""),
                            },
                            "finish_reason": data.get("done_reason") or "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": usage[0],
                        "completion_tokens": usage[1],
                        "total_tokens": usage[0] + usage[1],
                    },
                }
            )

        async def event_stream():
            try:
                async with client.stream("POST", OLLAMA_NATIVE, json=payload) as resp:
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        msg = chunk.get("message", {})
                        delta: dict = {}
                        if msg.get("content"):
                            delta["content"] = msg["content"]
                        if chunk.get("done"):
                            usage = (chunk.get("prompt_eval_count", 0), chunk.get("eval_count", 0))
                            yield f'data: {{"id":"chatcmpl-{uuid.uuid4().hex[:24]}","object":"chat.completion.chunk","model":"{chunk.get("model", payload["model"])}","choices":[{{"index":0,"delta":{{}},"finish_reason":"{chunk.get("done_reason") or "stop"}"}}],"usage":{{"prompt_tokens":{usage[0]},"completion_tokens":{usage[1]},"total_tokens":{usage[0] + usage[1]}}}}}\n\n'
                            yield "data: [DONE]\n\n"
                            return
                        if delta:
                            yield f'data: {{"id":"chatcmpl-{uuid.uuid4().hex[:24]}","object":"chat.completion.chunk","model":"{chunk.get("model", payload["model"])}","choices":[{{"index":0,"delta":{json.dumps(delta)},"finish_reason":null}}]}}\n\n'
            except Exception as e:  # keep the stream alive for the client
                yield f'data: {{"error":"{str(e)}"}}\n\n'
                yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")
