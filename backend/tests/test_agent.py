from app import agent, fallbacks

CTX = {"tenant_name": "Lena Hoffmann"}


def test_generate_uses_fallback_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert agent.generate("tenant_checkin", CTX) == fallbacks.TEXT["tenant_checkin"].format(**CTX)


def test_generate_fallback_on_exception(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def boom(*a, **k):
        raise RuntimeError("network down")

    monkeypatch.setattr(agent, "_call_openai", boom)
    assert agent.generate("tenant_checkin", CTX) == fallbacks.TEXT["tenant_checkin"].format(**CTX)


def test_all_kinds_have_fallbacks():
    assert set(fallbacks.TEXT) == set(agent.KINDS)
