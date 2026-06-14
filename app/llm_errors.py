from __future__ import annotations


def get_llm_error_status_code(exc: Exception) -> int | None:
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code

    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code

    response = getattr(exc, "response", None)
    response_status_code = getattr(response, "status_code", None)
    if isinstance(response_status_code, int):
        return response_status_code

    return None


def format_llm_error(exc: Exception) -> str:
    status_code = get_llm_error_status_code(exc)

    if status_code == 401:
        return "LLM API authorization failed. Check the configured API key."

    if status_code == 403:
        return "The LLM API request was forbidden. Check the configured API key and permissions."

    if status_code == 429:
        return "The LLM API request was rate-limited. Please try again later."

    if status_code is not None and 500 <= status_code <= 599:
        return "The LLM API request failed due to an internal server error. Please try again later."

    message = str(exc).strip()
    if not message:
        return "An error occurred while processing the LLM API request."

    return f"An error occurred while processing the LLM API request: {message}"
