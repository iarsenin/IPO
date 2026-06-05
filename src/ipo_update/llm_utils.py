from __future__ import annotations

import importlib.util
import json
import time
from dataclasses import dataclass
from typing import Any

from .logger import get_logger

# Maximum retries for transient API errors (rate-limit, network, server errors).
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 5  # seconds; doubles each retry


@dataclass(frozen=True)
class LlmUsage:
    task: str
    label: str | None
    model: str
    web_search: bool
    input_tokens: int | None
    cached_input_tokens: int
    output_tokens: int | None
    reasoning_tokens: int
    total_tokens: int | None
    web_search_calls: int
    estimated_cost_usd: float | None


@dataclass(frozen=True)
class LlmResponse:
    text: str
    citations: list[dict]
    usage: LlmUsage | None = None


_MODEL_PRICING_PER_1M = {
    "gpt-5.5": {"input": 5.00, "cached_input": 0.50, "output": 30.00},
    "gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "output": 4.50},
    "gpt-5.4-nano": {"input": 0.20, "cached_input": 0.02, "output": 1.25},
    "gpt-5.4": {"input": 2.50, "cached_input": 0.25, "output": 15.00},
    "gpt-5.2-chat-latest": {"input": 1.75, "cached_input": 0.175, "output": 14.00},
    "gpt-5.2": {"input": 1.75, "cached_input": 0.175, "output": 14.00},
    "gpt-5-mini": {"input": 0.25, "cached_input": 0.025, "output": 2.00},
    "gpt-5-nano": {"input": 0.05, "cached_input": 0.005, "output": 0.40},
    "gpt-5": {"input": 1.25, "cached_input": 0.125, "output": 10.00},
}
_WEB_SEARCH_COST_PER_CALL_USD = 10.00 / 1000
_USAGE_EVENTS: list[LlmUsage] = []


def build_openai_client(api_key: str):
    """Create an OpenAI client.  Returns None if the openai package is missing."""
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


def validate_openai_api_key(client, model: str) -> None:
    """Verify the API key is valid and the account has credits.

    Makes a tiny chat-completion call using the requested model so we fail
    fast instead of discovering a bad key or missing model access deep into
    the run.

    Raises
    ------
    SystemExit  if the key is invalid, expired, or the account has no credits.
    """
    logger = get_logger(__name__)
    logger.info(f"Validating OpenAI API key (model={model}) …")

    try:
        # Minimal call to verify authentication, billing, and model access.
        # Use max_completion_tokens (newer models) with max_tokens fallback.
        try:
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_completion_tokens=5,
            )
        except Exception as inner:
            inner_msg = str(inner).lower()
            # "max_completion_tokens" not supported → try legacy parameter
            if "max_completion_tokens" in inner_msg:
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=5,
                )
            # "max_tokens … was reached" → the call ran, meaning auth is fine
            elif "max_tokens" in inner_msg and "reached" in inner_msg:
                logger.info("OpenAI API key is valid (model responded, hit token limit as expected).")
                return
            else:
                raise
        logger.info("OpenAI API key is valid and account is funded.")
    except Exception as exc:
        exc_type = type(exc).__name__
        exc_msg = str(exc)[:300]

        # Import error types at runtime (openai may not be installed).
        try:
            from openai import AuthenticationError, PermissionDeniedError
        except ImportError:
            AuthenticationError = PermissionDeniedError = None

        if AuthenticationError and isinstance(exc, AuthenticationError):
            logger.critical(f"INVALID API KEY: {exc_msg}")
            raise SystemExit(
                "ERROR: OpenAI API key is invalid.  "
                "Check OPENAI_API_KEY in .env and verify the key on https://platform.openai.com/api-keys"
            ) from exc

        if PermissionDeniedError and isinstance(exc, PermissionDeniedError):
            logger.critical(f"PERMISSION DENIED (likely no credits): {exc_msg}")
            raise SystemExit(
                "ERROR: OpenAI account permission denied (likely insufficient credits).  "
                "Check billing at https://platform.openai.com/settings/organization/billing/overview"
            ) from exc

        # Catch quota / billing errors that surface as generic API errors
        lower_msg = exc_msg.lower()
        if any(kw in lower_msg for kw in ("insufficient_quota", "billing", "exceeded", "deactivated")):
            logger.critical(f"BILLING / QUOTA ERROR: {exc_msg}")
            raise SystemExit(
                f"ERROR: OpenAI billing/quota problem — {exc_msg}\n"
                "Top up credits at https://platform.openai.com/settings/organization/billing/overview"
            ) from exc

        # Any other error during validation is still a showstopper.
        logger.critical(f"OpenAI API validation failed: {exc_type} — {exc_msg}")
        raise SystemExit(
            f"ERROR: Could not validate OpenAI API key ({exc_type}).  "
            f"Details: {exc_msg}"
        ) from exc


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


def _object_to_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return value.dict()
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return {}


def _nested_dict(value: Any, key: str) -> dict:
    data = _object_to_dict(value)
    nested = data.get(key)
    return _object_to_dict(nested)


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _first_int(data: dict, *keys: str) -> int | None:
    for key in keys:
        value = _int_or_none(data.get(key))
        if value is not None:
            return value
    return None


def _pricing_for_model(model: str) -> dict[str, float] | None:
    normalized = model.lower().strip()
    for model_prefix in sorted(_MODEL_PRICING_PER_1M, key=len, reverse=True):
        if normalized == model_prefix or normalized.startswith(f"{model_prefix}-"):
            return _MODEL_PRICING_PER_1M[model_prefix]
    return None


def _count_web_search_calls(response) -> int:
    return sum(
        1
        for item in _get_output_items(response)
        if getattr(item, "type", None) == "web_search_call"
    )


def _estimate_cost_usd(
    model: str,
    input_tokens: int | None,
    cached_input_tokens: int,
    output_tokens: int | None,
    web_search_calls: int,
) -> float | None:
    pricing = _pricing_for_model(model)
    if pricing is None or input_tokens is None or output_tokens is None:
        return None

    billable_input_tokens = max(input_tokens - cached_input_tokens, 0)
    token_cost = (
        billable_input_tokens * pricing["input"]
        + cached_input_tokens * pricing["cached_input"]
        + output_tokens * pricing["output"]
    ) / 1_000_000
    search_cost = web_search_calls * _WEB_SEARCH_COST_PER_CALL_USD
    return token_cost + search_cost


def _usage_from_response(
    response,
    *,
    task: str,
    label: str | None,
    model: str,
    web_search: bool,
) -> LlmUsage:
    usage_data = _object_to_dict(getattr(response, "usage", None))
    input_details = _nested_dict(usage_data, "input_tokens_details")
    output_details = _nested_dict(usage_data, "output_tokens_details")
    prompt_details = _nested_dict(usage_data, "prompt_tokens_details")
    completion_details = _nested_dict(usage_data, "completion_tokens_details")

    input_tokens = _first_int(usage_data, "input_tokens", "prompt_tokens")
    output_tokens = _first_int(usage_data, "output_tokens", "completion_tokens")
    total_tokens = _first_int(usage_data, "total_tokens")
    cached_input_tokens = (
        _first_int(input_details, "cached_tokens")
        or _first_int(prompt_details, "cached_tokens")
        or 0
    )
    reasoning_tokens = (
        _first_int(output_details, "reasoning_tokens")
        or _first_int(completion_details, "reasoning_tokens")
        or 0
    )
    web_search_calls = _count_web_search_calls(response) if web_search else 0
    estimated_cost_usd = _estimate_cost_usd(
        model=model,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        web_search_calls=web_search_calls,
    )
    usage = LlmUsage(
        task=task,
        label=label,
        model=model,
        web_search=web_search,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        web_search_calls=web_search_calls,
        estimated_cost_usd=estimated_cost_usd,
    )
    _USAGE_EVENTS.append(usage)
    return usage


def _format_cost(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"${value:.4f}"


def _log_usage_event(usage: LlmUsage) -> None:
    logger = get_logger(__name__)
    label = f" label={usage.label}" if usage.label else ""
    logger.info(
        "OpenAI usage: "
        f"task={usage.task}{label} model={usage.model} web_search={usage.web_search} "
        f"input_tokens={usage.input_tokens if usage.input_tokens is not None else 'unknown'} "
        f"cached_input_tokens={usage.cached_input_tokens} "
        f"output_tokens={usage.output_tokens if usage.output_tokens is not None else 'unknown'} "
        f"reasoning_tokens={usage.reasoning_tokens} "
        f"total_tokens={usage.total_tokens if usage.total_tokens is not None else 'unknown'} "
        f"web_search_calls={usage.web_search_calls} "
        f"estimated_cost={_format_cost(usage.estimated_cost_usd)}"
    )


def log_usage_summary() -> None:
    """Log aggregate OpenAI token/search usage for this process."""
    logger = get_logger(__name__)
    if not _USAGE_EVENTS:
        logger.info("OpenAI usage summary: no metered model calls recorded")
        return

    summary: dict[tuple[str, str, bool], dict[str, float | int | None]] = {}
    for event in _USAGE_EVENTS:
        key = (event.task, event.model, event.web_search)
        bucket = summary.setdefault(
            key,
            {
                "calls": 0,
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
                "reasoning_tokens": 0,
                "total_tokens": 0,
                "web_search_calls": 0,
                "estimated_cost_usd": 0.0,
                "unknown_costs": 0,
            },
        )
        bucket["calls"] = int(bucket["calls"]) + 1
        bucket["input_tokens"] = int(bucket["input_tokens"]) + (event.input_tokens or 0)
        bucket["cached_input_tokens"] = int(bucket["cached_input_tokens"]) + event.cached_input_tokens
        bucket["output_tokens"] = int(bucket["output_tokens"]) + (event.output_tokens or 0)
        bucket["reasoning_tokens"] = int(bucket["reasoning_tokens"]) + event.reasoning_tokens
        bucket["total_tokens"] = int(bucket["total_tokens"]) + (event.total_tokens or 0)
        bucket["web_search_calls"] = int(bucket["web_search_calls"]) + event.web_search_calls
        if event.estimated_cost_usd is None:
            bucket["unknown_costs"] = int(bucket["unknown_costs"]) + 1
        else:
            bucket["estimated_cost_usd"] = float(bucket["estimated_cost_usd"]) + event.estimated_cost_usd

    logger.info("OpenAI usage summary by task/model:")
    total_cost = 0.0
    unknown_costs = 0
    for (task, model, web_search), bucket in sorted(summary.items()):
        estimated = float(bucket["estimated_cost_usd"])
        total_cost += estimated
        unknown_costs += int(bucket["unknown_costs"])
        cost_text = _format_cost(estimated)
        if bucket["unknown_costs"]:
            cost_text += f" (+ {bucket['unknown_costs']} unknown)"
        logger.info(
            f"- task={task} model={model} web_search={web_search} "
            f"calls={bucket['calls']} input_tokens={bucket['input_tokens']} "
            f"cached_input_tokens={bucket['cached_input_tokens']} "
            f"output_tokens={bucket['output_tokens']} reasoning_tokens={bucket['reasoning_tokens']} "
            f"web_search_calls={bucket['web_search_calls']} estimated_cost={cost_text}"
        )
    total_text = _format_cost(total_cost)
    if unknown_costs:
        total_text += f" (+ {unknown_costs} unknown call costs)"
    logger.info(f"OpenAI usage total estimated_cost={total_text}")


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


def _call_responses(
    client,
    model: str,
    prompt: str,
    *,
    web_search: bool,
    task: str,
    label: str | None = None,
    max_output_tokens: int | None = None,
) -> LlmResponse:
    """Call the OpenAI Responses API or fall back to Chat Completions.

    Retries transient errors (rate-limit, server 5xx, network) up to
    ``_MAX_RETRIES`` times with exponential back-off.  Auth / billing
    errors are raised immediately so the caller can abort the run.
    """
    logger = get_logger(__name__)

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            if hasattr(client, "responses"):
                kwargs = {
                    "model": model,
                    "input": prompt,
                }
                if max_output_tokens is not None:
                    kwargs["max_output_tokens"] = max_output_tokens
                if web_search:
                    kwargs["tools"] = [{"type": "web_search"}]
                    kwargs["include"] = ["web_search_call.action.sources"]

                response = client.responses.create(**kwargs)
                result = extract_citations_from_responses(response)
                usage = _usage_from_response(
                    response,
                    task=task,
                    label=label,
                    model=model,
                    web_search=web_search,
                )
                _log_usage_event(usage)
                logger.info(
                    f"OpenAI Responses API success: task={task} model={model} "
                    f"web_search={web_search} {len(result.text)} chars, "
                    f"{len(result.citations)} citations (attempt {attempt})"
                )
                return LlmResponse(text=result.text, citations=result.citations, usage=usage)

            if web_search:
                logger.warning("Responses API not available, falling back to Chat Completions API (no web search)")
            else:
                logger.warning("Responses API not available, falling back to Chat Completions API")
            chat_kwargs = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
            }
            if max_output_tokens is not None:
                chat_kwargs["max_completion_tokens"] = max_output_tokens
            completion = client.chat.completions.create(**chat_kwargs)
            text = completion.choices[0].message.content.strip()
            usage = _usage_from_response(
                completion,
                task=task,
                label=label,
                model=model,
                web_search=False,
            )
            _log_usage_event(usage)
            return LlmResponse(text=text, citations=[], usage=usage)

        except Exception as exc:
            exc_type = type(exc).__name__
            exc_msg = str(exc)[:300]

            # Fatal errors — do NOT retry.
            try:
                from openai import AuthenticationError, PermissionDeniedError
            except ImportError:
                AuthenticationError = PermissionDeniedError = None

            if AuthenticationError and isinstance(exc, AuthenticationError):
                logger.critical(f"OpenAI authentication failed: {exc_msg}")
                raise SystemExit(
                    "ERROR: OpenAI API key is invalid or revoked.  "
                    "The program cannot continue."
                ) from exc

            if PermissionDeniedError and isinstance(exc, PermissionDeniedError):
                logger.critical(f"OpenAI permission denied: {exc_msg}")
                raise SystemExit(
                    "ERROR: OpenAI permission denied (likely no credits).  "
                    "The program cannot continue."
                ) from exc

            lower_msg = exc_msg.lower()
            if any(kw in lower_msg for kw in ("insufficient_quota", "billing", "deactivated")):
                logger.critical(f"OpenAI billing/quota error: {exc_msg}")
                raise SystemExit(
                    f"ERROR: OpenAI billing/quota issue — {exc_msg}"
                ) from exc

            # Transient errors — retry with back-off.
            if attempt < _MAX_RETRIES:
                wait = _RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    f"OpenAI API error (attempt {attempt}/{_MAX_RETRIES}): "
                    f"{exc_type} — {exc_msg}.  Retrying in {wait}s …"
                )
                time.sleep(wait)
            else:
                logger.error(
                    f"OpenAI API error (attempt {attempt}/{_MAX_RETRIES}): "
                    f"{exc_type} — {exc_msg}.  Giving up."
                )
                raise


def call_responses_with_web_search(
    client,
    model: str,
    prompt: str,
    *,
    task: str = "llm_web",
    label: str | None = None,
    max_output_tokens: int | None = None,
) -> LlmResponse:
    return _call_responses(
        client,
        model,
        prompt,
        web_search=True,
        task=task,
        label=label,
        max_output_tokens=max_output_tokens,
    )


def call_responses_text_only(
    client,
    model: str,
    prompt: str,
    *,
    task: str = "llm_text",
    label: str | None = None,
    max_output_tokens: int | None = None,
) -> LlmResponse:
    return _call_responses(
        client,
        model,
        prompt,
        web_search=False,
        task=task,
        label=label,
        max_output_tokens=max_output_tokens,
    )


def extract_json_block(text: str) -> object | None:
    """Extract the first JSON object or array from a response string.
    
    Handles markdown code fences like ```json ... ``` and raw JSON.
    """
    import re
    
    if not text:
        return None
    
    logger = get_logger(__name__)

    # First, try to extract from markdown code fences
    # Match ```json ... ``` or ``` ... ``` blocks
    code_fence_pattern = r'```(?:json)?\s*\n?([\s\S]*?)\n?```'
    matches = re.findall(code_fence_pattern, text)
    for match in matches:
        match = match.strip()
        if match.startswith('{') or match.startswith('['):
            try:
                return json.loads(match)
            except json.JSONDecodeError:
                continue  # Try next match or fall through to raw extraction

    # Fallback: find raw JSON in text
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
    in_string = False
    escape_next = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
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
    except json.JSONDecodeError as exc:
        logger.warning(f"Failed to parse JSON block from response: {exc}")
        # Log first 200 chars of the raw block for debugging
        logger.debug(f"Raw JSON block (first 200 chars): {raw[:200]}")
        return None
