import logging

import server


def test_groq_chat_diagnostic_log_excludes_messages_and_secrets(caplog):
    secret_message = "candidate-secret@example.com bearer-secret-value"
    kwargs = {
        "model": "test-model",
        "messages": [{"role": "user", "content": secret_message}],
        "temperature": 0.7,
    }

    with caplog.at_level(logging.INFO, logger="server"):
        server._log_groq_chat_diagnostic("diagnostic-test-id", kwargs)

    assert len(caplog.records) == 1
    message = caplog.records[0].message
    assert "GROQ_CHAT_DIAGNOSTIC" in message
    assert "request_id=diagnostic-test-id" in message
    assert "model=test-model" in message
    assert "has_tools=False" in message
    assert "has_tool_choice=False" in message
    assert "has_functions=False" in message
    assert "has_function_call=False" in message
    assert "has_response_format=False" in message
    assert secret_message not in message
    assert "candidate-secret@example.com" not in message
    assert "bearer-secret-value" not in message
