from __future__ import annotations

import importlib.util
import json
from dataclasses import dataclass

from .logger import get_logger


@dataclass(frozen=True)
class LlmResponse:
    text: str
    citations: list[dict]


def build_openai_client(api_key: str):
    if importlib.util.find_spec("openai") is None:
        return None
    from openai import OpenAI
    import httpx
    import os

    try:
        old_proxy_vars = {}
        proxy_vars = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]
        for var in proxy_vars:
            if var in os.environ:
                old_proxy_vars[var] = os.environ.pop(var)

        try:
            http_client = httpx.Client(timeout=600.0)
            client = OpenAI(api_key=api_key, http_client=http_client, timeout=600.0)
            return client
        finally:
            for var, value in old_proxy_vars.items():
                os.environ[var] = value
    except Exception as exc:
        logger = get_logger(__name__)
        logger.error(f"Failed to create OpenAI client: {type(exc).__name__} - {str(exc)[:200]}")
        return None


def _extract_citations_from_item(item) -> list[dict]:
    citations = []
    if not (hasattr(item, "type") and item.type == "web_search_call"):
        return citations
    if not (hasattr(item, "action") and item.action):
        return citations
    if not (hasattr(item.action, "sources") and item.action.sources):
        return citations

    sources_list = item.action.sources
    if not isinstance(sources_list, (list, tuple)):
        sources_list = [sources_list]
    for source in sources_list:
        if source:
            citations.append(
                {
                    "title": getattr(source, "title", ""),
                    "url": getattr(source, "url", ""),
                    "snippet": getattr(source, "snippet", ""),
                }
            )
    return citations


def _extract_text_from_item(item) -> list[str]:
    text_parts = []
    if not (hasattr(item, "type") and item.type == "message"):
        return text_parts
    if not (hasattr(item, "content") and item.content):
        return text_parts

    content_list = item.content
    if not isinstance(content_list, (list, tuple)):
        content_list = [content_list]
    for content_block in content_list:
        if content_block is None:
            continue
        if hasattr(content_block, "output_text") and content_block.output_text:
            text_parts.append(str(content_block.output_text))
        elif hasattr(content_block, "text") and content_block.text:
            text_parts.append(str(content_block.text))
    return text_parts


def _get_output_items(response) -> list:
    if not hasattr(response, "output") or response.output is None:
        return []
    if not hasattr(response.output, "__iter__") or isinstance(response.output, str):
        return []
    try:
        return list(response.output)
    except Exception as exc:
        logger = get_logger(__name__)
        logger.debug(f"Could not convert response.output to list: {exc}")
        return []


def extract_citations_from_responses(response) -> LlmResponse:
    logger = get_logger(__name__)
    citations: list[dict] = []

    try:
        if hasattr(response, "output_text") and response.output_text is not None:
            full_text = str(response.output_text).strip()
            for item in _get_output_items(response):
                if item is None:
                    continue
                citations.extend(_extract_citations_from_item(item))
            return LlmResponse(text=full_text, citations=citations)

        text_parts: list[str] = []
        for item in _get_output_items(response):
            if item is None:
                continue
            text_parts.extend(_extract_text_from_item(item))
            citations.extend(_extract_citations_from_item(item))
        full_text = "".join(text_parts).strip()

        if not full_text and hasattr(response, "text"):
            full_text = str(response.text).strip()
            logger.info(f"Found text in response.text attribute: {len(full_text)} chars")

        if not full_text:
            logger.warning("No text extracted from Responses API response")
        return LlmResponse(text=full_text, citations=citations)
    except Exception as exc:
        logger.error(f"Could not extract text/citations from Responses API: {type(exc).__name__} - {exc}")
        return LlmResponse(text="", citations=[])


def call_responses_with_web_search(client, model: str, prompt: str) -> LlmResponse:
    logger = get_logger(__name__)
    if hasattr(client, "responses"):
        response = client.responses.create(
            model=model,
            input=prompt,
            tools=[{"type": "web_search"}],
            include=["web_search_call.action.sources"],
        )
        result = extract_citations_from_responses(response)
        logger.info(f"OpenAI Responses API success: {len(result.text)} chars, {len(result.citations)} citations")
        return result

    logger.warning("Responses API not available, falling back to Chat Completions API (no web search)")
    completion = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    text = completion.choices[0].message.content.strip()
    return LlmResponse(text=text, citations=[])


def extract_json_block(text: str) -> object | None:
    """Extract the first JSON object or array from a response string."""
    if not text:
        return None

    # Prefer the earliest JSON-looking block to avoid capturing prose.
    start_obj = text.find("{")
    start_arr = text.find("[")
    if start_obj == -1 and start_arr == -1:
        return None

    if start_obj == -1:
        start = start_arr
        open_char, close_char = "[", "]"
    elif start_arr == -1:
        start = start_obj
        open_char, close_char = "{", "}"
    else:
        if start_arr < start_obj:
            start = start_arr
            open_char, close_char = "[", "]"
        else:
            start = start_obj
            open_char, close_char = "{", "}"

    depth = 0
    end = None
    for idx in range(start, len(text)):
        ch = text[idx]
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                end = idx + 1
                break

    if end is None:
        return None

    raw = text[start:end]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger = get_logger(__name__)
        logger.warning("Failed to parse JSON block from response")
        return None
