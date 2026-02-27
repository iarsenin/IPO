from __future__ import annotations

import importlib.util
import json
import time
from dataclasses import dataclass

from .logger import get_logger

# Maximum retries for transient API errors (rate-limit, network, server errors).
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 5  # seconds; doubles each retry


@dataclass(frozen=True)
class LlmResponse:
    text: str
    citations: list[dict]


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

    Makes a tiny chat-completion call (max_tokens=1) using the same model
    configured in .env so we fail fast instead of discovering a bad key
    thirty minutes into the run.

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
    """Call the OpenAI Responses API (with web search) or fall back to Chat Completions.

    Retries transient errors (rate-limit, server 5xx, network) up to
    ``_MAX_RETRIES`` times with exponential back-off.  Auth / billing
    errors are raised immediately so the caller can abort the run.
    """
    logger = get_logger(__name__)

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            if hasattr(client, "responses"):
                response = client.responses.create(
                    model=model,
                    input=prompt,
                    tools=[{"type": "web_search"}],
                    include=["web_search_call.action.sources"],
                )
                result = extract_citations_from_responses(response)
                logger.info(
                    f"OpenAI Responses API success: {len(result.text)} chars, "
                    f"{len(result.citations)} citations (attempt {attempt})"
                )
                return result

            logger.warning("Responses API not available, falling back to Chat Completions API (no web search)")
            completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            text = completion.choices[0].message.content.strip()
            return LlmResponse(text=text, citations=[])

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
