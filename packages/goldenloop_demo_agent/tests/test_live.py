"""Exercise the installed Microsoft framework with fake model responses, not cloud calls."""
import asyncio
import builtins
import json

import pytest

from goldenloop_demo_agent import run_case
from goldenloop_eval import Case, evaluate


def settings(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://synthetic.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_COMPLETION_MODEL", "unit-deployment")
    monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "unit-only-credential")


def test_missing_optional_dependency(monkeypatch):
    settings(monkeypatch)
    original = builtins.__import__
    def no_framework(name, *args, **kwargs):
        if name == "agent_framework":
            raise ImportError("simulated missing optional package")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_framework)
    case = Case(title="test", turns=[{"user": "Look up C-123"}])
    with pytest.raises(RuntimeError, match=r"Install goldenloop-demo-agent\[live\]"):
        asyncio.run(run_case(case, mode="live"))


@pytest.mark.parametrize("failure", [None, "provider", "tool", "malformed_arguments"])
def test_real_framework_loop_and_generated_history(monkeypatch, failure):
    pytest.importorskip("agent_framework.openai")
    import openai
    from openai.types.chat import ChatCompletion

    settings(monkeypatch)
    requests = []
    clients = []
    real_client = openai.AsyncAzureOpenAI

    # Keep the concrete SDK type intact: the provider uses isinstance for routing.
    original_init = real_client.__init__
    def patched_init(self, **kwargs):
        original_init(self, **kwargs)
        clients.append(self)
        async def create(**params):
            requests.append(params)
            if failure == "provider":
                raise RuntimeError("unit-only-credential")
            number = len(requests)
            if number == 1:
                arguments = json.dumps({"customer_id": "C-000" if failure == "tool" else "C-123"})
                message = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call-1", "type": "function", "function": {
                        "name": "lookup_customer", "arguments": "not-json" if failure == "malformed_arguments" else arguments,
                    },
                }]}
                finish_reason = "tool_calls"
            else:
                message = {"role": "assistant", "content": f"Generated C-123 customer answer {number}."}
                finish_reason = "stop"
            return ChatCompletion.model_validate({
                "id": f"response-{number}", "object": "chat.completion", "created": 0,
                "model": "unit-deployment", "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
            })
        monkeypatch.setattr(self.chat.completions, "create", create)
    monkeypatch.setattr(real_client, "__init__", patched_init)
    case = Case(title="test", turns=[
        {"user": "Look up customer C-123", "reference_answer": "REFERENCE NEVER IN HISTORY"},
        {"user": "Which customer was it?", "reference_answer": "REFERENCE NEVER IN HISTORY"},
    ], checks=[{"kind": "tool_arguments", "turn": 0, "config": {
        "tool": "lookup_customer", "path": "customer_id", "operator": "equals", "value": "C-123",
    }}])
    obs = asyncio.run(run_case(case, mode="live"))
    assert all(client.is_closed() for client in clients)
    assert "unit-only-credential" not in obs.model_dump_json()
    if failure:
        assert evaluate(case, obs).gate == "error"
    else:
        assert obs.error is None, obs.error
        assert obs.trace_complete
        assert obs.tool_calls[0].arguments == {"customer_id": "C-123"}
        assert "Synthetic Alpine Bikes" in str(obs.tool_calls[0].result)
        assert obs.tool_calls[0].id == "call-1"
        assert evaluate(case, obs).gate == "pass"
        history = json.dumps(requests[-1]["messages"])
        assert "Generated C-123 customer answer 2." in history
        assert "REFERENCE NEVER IN HISTORY" not in history
        assert obs.usage["total_token_count"] > 0
        assert obs.latency_ms >= 0


def test_insecure_endpoint_rejected(monkeypatch):
    settings(monkeypatch)
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "http://localhost")
    with pytest.raises(ValueError, match="HTTPS"):
        asyncio.run(run_case(Case(title="test", turns=[{"user": "hello"}]), mode="live"))
