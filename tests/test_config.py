from app.config import ROOT, expand_env, get_settings, load_yaml_settings


def test_expand_env_default_and_value() -> None:
    assert expand_env("a ${X:-dflt} b", {}) == "a dflt b"
    assert expand_env("${X:-dflt}", {"X": "val"}) == "val"
    assert expand_env("${Y}", {}) == ""


def test_yaml_settings_load() -> None:
    cfg = load_yaml_settings(ROOT / "config" / "settings.yaml", {"EXTRACT_MODEL": "m1"})
    assert cfg.models.extract == "m1"
    assert cfg.ranking.w_retrieval == 0.65
    assert cfg.trust.deltas["money"] == -25
    assert set(cfg.embeddings.models) == {"minilm", "bge-small"}
    assert cfg.agent.max_steps == 6


def test_provider_resolves_to_fake_without_key() -> None:
    assert get_settings().llm_provider == "fake"
