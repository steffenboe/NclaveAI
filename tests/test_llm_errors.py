from app.llm_errors import get_llm_error_status_code
from app.llm_errors import format_llm_error
from tests.helpers import FakeLLMError, FakeResponse

def test_extracts_status_code_attribute():
    exc = FakeLLMError("Unauthorized", status_code=401)

    assert get_llm_error_status_code(exc) == 401

def test_extract_nested_response_status_code():
    exc = Exception("Unauthorized")
    exc.response = FakeResponse()

    assert get_llm_error_status_code(exc) == 401

def test_formats_401_as_api_key_message():
    exc = FakeLLMError("HTTP 401 Unauthorized", status_code=401)

    assert (format_llm_error(exc)
        == "LLM API authorization failed. Check the configured API key."
    )

def test_formats_403_as_api_key_message():
    exc = FakeLLMError("HTTP 403 Forbidden", status_code=403)

    assert (format_llm_error(exc)
        == "The LLM API request was forbidden. Check the configured API key and permissions."
    )

def test_formats_429_as_api_key_message():
    exc = FakeLLMError("HTTP 429 Too Many Requests", status_code=429)

    assert (format_llm_error(exc)
        == "The LLM API request was rate-limited. Please try again later."
    )

def test_formats_500_as_api_key_message():
    exc = FakeLLMError("HTTP 500 Internal Server Error", status_code=500)

    assert (format_llm_error(exc)
        == "The LLM API request failed due to an internal server error. Please try again later."
    )

def test_no_status_code_returns_generic_message():
    exc = FakeLLMError("Some error without status code")

    assert (format_llm_error(exc)
        == "An error occurred while processing the LLM API request: Some error without status code"
    )
