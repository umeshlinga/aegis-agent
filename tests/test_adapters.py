"""Real provider adapter tests.

No network and no real keys: transports are stubbed, and the missing-key
path is exercised with the environment cleared. The adapters must fail
cleanly with a typed error when no key is set, report token counts and
latency when a call succeeds, and flag every cost as an estimate.
"""

import pytest

from aegis.models.adapter import ModelRequest
from aegis.models.fake import ScriptedFakeModel
from aegis.models.http_adapters import (
    AnthropicAdapter,
    LocalAdapter,
    OpenAIAdapter,
    load_dotenv,
)
from aegis.models.pricing import estimate_cost_usd, known_model
from aegis.models.router import ModelRouter
from aegis.models.adapter import MissingAPIKeyError, ModelError, ProviderError
from aegis.trace.store import TraceWriter, read_events

REQ = ModelRequest(step_kind="plan", prompt="hello")


def _no_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_anthropic_missing_key_is_typed_and_clean(monkeypatch):
    _no_keys(monkeypatch)
    with pytest.raises(MissingAPIKeyError) as caught:
        AnthropicAdapter().complete(REQ)
    assert caught.value.provider == "anthropic"
    assert isinstance(caught.value, ModelError)


def test_openai_missing_key_is_typed_and_clean(monkeypatch):
    _no_keys(monkeypatch)
    with pytest.raises(MissingAPIKeyError) as caught:
        OpenAIAdapter().complete(REQ)
    assert caught.value.provider == "openai"


def test_anthropic_stub_transport_parses_and_prices(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    seen = {}

    def transport(url, headers, body):
        seen.update(url=url, headers=headers, body=body)
        assert headers["x-api-key"] == "test-key-not-real"
        return {
            "content": [{"type": "text", "text": "planned"}],
            "usage": {"input_tokens": 1000, "output_tokens": 500},
        }

    adapter = AnthropicAdapter(transport=transport)
    response = adapter.complete(REQ)
    assert response.text == "planned"
    assert response.model == "anthropic:claude-sonnet-4-5"
    assert response.input_tokens == 1000
    assert response.output_tokens == 500
    assert response.latency_ms >= 0
    # 1000 in at $3/1M + 500 out at $15/1M = 0.003 + 0.0075
    assert response.cost_usd == pytest.approx(0.0105)
    assert response.cost_is_estimate is True
    assert "test-key-not-real" not in response.text


def test_openai_stub_transport_parses_and_prices(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")

    def transport(url, headers, body):
        assert headers["Authorization"] == "Bearer test-key-not-real"
        return {
            "choices": [{"message": {"content": "acted"}}],
            "usage": {"prompt_tokens": 2000, "completion_tokens": 1000},
        }

    response = OpenAIAdapter(transport=transport).complete(REQ)
    assert response.text == "acted"
    assert response.input_tokens == 2000
    assert response.output_tokens == 1000
    assert response.cost_usd == pytest.approx(
        estimate_cost_usd("gpt-5-mini", 2000, 1000)
    )
    assert response.cost_is_estimate is True


def test_local_adapter_needs_no_key_and_parses_counts():
    def transport(url, headers, body):
        assert url == "http://localhost:11434/api/chat"
        assert "Authorization" not in headers
        return {
            "message": {"content": "local answer"},
            "prompt_eval_count": 42,
            "eval_count": 7,
        }

    response = LocalAdapter(transport=transport).complete(REQ)
    assert response.text == "local answer"
    assert response.input_tokens == 42
    assert response.output_tokens == 7
    assert response.cost_usd == 0.0
    assert response.cost_is_estimate is True


def test_local_adapter_unreachable_is_provider_error():
    def transport(url, headers, body):
        raise ProviderError("local", "connection failed: refused")

    with pytest.raises(ProviderError):
        LocalAdapter(transport=transport).complete(REQ)


def test_malformed_provider_response_is_provider_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    adapter = AnthropicAdapter(transport=lambda url, h, b: {"nope": True})
    with pytest.raises(ProviderError):
        adapter.complete(REQ)


def test_router_falls_back_when_key_missing(monkeypatch, tmp_path):
    _no_keys(monkeypatch)
    fake = ScriptedFakeModel({"plan": "from fake"})
    trace = TraceWriter(tmp_path / "trace.jsonl")
    router = ModelRouter(
        {"anthropic": AnthropicAdapter(), "fake": fake},
        {"plan": ["anthropic", "fake"]},
        trace=trace,
    )
    response = router.complete("run-1", REQ)
    assert response.text == "from fake"
    events = list(read_events(trace.path, run_id="run-1"))
    kinds = [e["type"] for e in events]
    assert kinds == ["model_call_failed", "model_call"]
    assert events[1]["payload"]["cost_is_estimate"] is False  # fake: exact zero


def test_pricing_table_and_unknown_model():
    assert known_model("claude-sonnet-4-5")
    assert estimate_cost_usd("claude-sonnet-4-5", 1_000_000, 0) == pytest.approx(3.0)
    assert not known_model("no-such-model")
    assert estimate_cost_usd("no-such-model", 10**6, 10**6) == 0.0


def test_load_dotenv_sets_missing_only(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("AEGIS_TEST_A=from-file\nAEGIS_TEST_B=from-file\n")
    monkeypatch.setenv("AEGIS_TEST_B", "from-env")
    monkeypatch.delenv("AEGIS_TEST_A", raising=False)
    load_dotenv(env_file)
    import os

    assert os.environ["AEGIS_TEST_A"] == "from-file"
    assert os.environ["AEGIS_TEST_B"] == "from-env"
