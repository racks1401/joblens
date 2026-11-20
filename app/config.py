"""Configuration: secrets from .env (pydantic-settings) + tunables from config/settings.yaml."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
_VAR = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


class EnvSettings(BaseSettings):
    """Secrets and deployment values read from the environment / .env."""

    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    llm_api_key: str = ""
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_provider: str = "auto"  # auto | openai | fake
    judge_api_key: str = ""
    judge_base_url: str = ""
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    database_url: str = "postgresql+psycopg://joblens:joblens@localhost:5432/joblens"
    admin_api_key: str = ""
    demo_limits: bool = True  # DEMO_LIMITS=false turns the chat/paste/explain/resume rate limits off (tests)
    settings_file: str = str(ROOT / "config" / "settings.yaml")
    companies_file: str = str(ROOT / "config" / "companies.yaml")
    fastembed_cache: str = str(ROOT / "models" / "fastembed")
    embedder: str = "fastembed"  # fastembed | fake (tests)

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, v: object) -> object:
        """Tolerate stray spaces/quotes around values in .env (e.g. `LLM_API_KEY= gsk_...`)."""
        return v.strip().strip('"').strip("'").strip() if isinstance(v, str) else v


class ModelsCfg(BaseModel):
    extract: str
    explain: str
    agent: str
    judge: str
    profile: str = "openai/gpt-oss-20b"


class ProviderCfg(BaseModel):
    """One OpenAI-compatible endpoint. Each provider gets its own rate limiter."""

    tpm_limit: int = 0
    rpm_limit: int = 0
    model_params: dict[str, dict[str, Any]] = {}


class PromptsCfg(BaseModel):
    facts: str = "facts_v1"
    facts_paste: str = ""  # prompt for pasted jobs; empty = same as `facts`
    profile: str = "profile_v1"
    explain: str = "explain_v1"
    judge: str = "judge_v1"
    agent: str = "agent_v1"


class LLMCfg(BaseModel):
    timeout_s: float = 60
    max_retries: int = 2
    max_input_tokens: int = 6000
    tpm_limit: int = 0  # provider tokens-per-minute limit for pacing; 0 = no pacing
    extract_max_chars: int = 8000  # job text sent to the extractor
    model_params: dict[str, dict[str, Any]] = {}  # extra request body per model


class EmbModelCfg(BaseModel):
    name: str
    query_prefix: str = ""


class EmbeddingsCfg(BaseModel):
    default_model: str = "minilm"
    default_variant: str = "structured"
    models: dict[str, EmbModelCfg]
    batch_size: int = 64


class SearchCfg(BaseModel):
    rrf_k: int = 60
    candidates: int = 50


class RankingCfg(BaseModel):
    w_retrieval: float = 0.65
    w_skill: float = 0.35
    check_penalty: float = 0.05
    risky_factor: float = 0.7
    caution_factor: float = 0.9


class TrustCfg(BaseModel):
    levels: dict[str, int]
    deltas: dict[str, int]
    repost_min: int = 2
    unrealistic_intern_pay_inr_month: int = 200000
    classifier_path: str = "models/scam_clf.joblib"


class AgentCfg(BaseModel):
    max_steps: int = 6
    history_messages: int = 10
    summary_every: int = 6
    tool_timeout_s: float = 10
    temperature: float = 0.2


class BudgetCfg(BaseModel):
    daily_tokens: int = 300000  # per purpose, unless per_purpose names one
    per_purpose: dict[str, int] = {}  # e.g. {agent: 180000, explain: 40000}; each counts only its own calls

    def for_purpose(self, purpose: str) -> int:
        return self.per_purpose.get(purpose, self.daily_tokens)


class ActionLimit(BaseModel):
    per_session: int = 20  # per client session per day
    per_day: int = 200  # whole app per day


class LimitsCfg(BaseModel):
    """Demo rate limits for actions that call an LLM (the per-purpose token budgets apply as well)."""

    enabled: bool = True
    actions: dict[str, ActionLimit] = {}

    def for_action(self, action: str) -> ActionLimit:
        return self.actions.get(action, ActionLimit())


class IngestCfg(BaseModel):
    requests_per_second: float = 2
    timeout_s: float = 15
    retries: int = 3
    user_agent: str = "JobLens/0.1"
    max_description_chars: int = 20000
    repost_window_days: int = 90
    max_jobs_per_company: int = 0
    role_family_overrides: dict[str, dict[str, str]] = {}  # job_key -> {family, reason}; human-reviewed
    india_keywords: list[str] = []
    remote_keywords: list[str] = []
    remote_exclude: list[str] = []


class YamlSettings(BaseModel):
    """Typed view of config/settings.yaml."""

    models: ModelsCfg
    providers: dict[str, ProviderCfg] = {}
    role_providers: dict[str, str] = {}  # role -> provider name (default groq)
    prompts: PromptsCfg = PromptsCfg()
    llm: LLMCfg = LLMCfg()
    embeddings: EmbeddingsCfg
    search: SearchCfg = SearchCfg()
    ranking: RankingCfg = RankingCfg()
    trust: TrustCfg
    agent: AgentCfg = AgentCfg()
    budget: BudgetCfg = BudgetCfg()
    limits: LimitsCfg = LimitsCfg()
    ingest: IngestCfg = IngestCfg()


class Settings(BaseModel):
    """Everything the app needs, merged."""

    env: EnvSettings
    cfg: YamlSettings

    @property
    def llm_provider(self) -> str:
        """Resolved provider: 'openai' when a key is set (or forced), else 'fake'."""
        p = self.env.llm_provider.lower()
        if p in ("openai", "fake"):
            return p
        return "openai" if self.env.llm_api_key else "fake"

    def provider_for(self, role: str) -> str:
        """Provider name for a role (extract | profile | explain | agent | judge)."""
        return self.cfg.role_providers.get(role, "groq")

    def provider_endpoint(self, provider: str) -> tuple[str, str]:
        """(base_url, api_key) for a provider. Keys come only from the environment / .env."""
        if provider == "gemini":
            return self.env.gemini_base_url, self.env.gemini_api_key
        return self.env.llm_base_url, self.env.llm_api_key


def expand_env(text: str, environ: dict[str, str] | None = None) -> str:
    """Replace ${VAR} and ${VAR:-default} with environment values."""
    env = os.environ if environ is None else environ

    def sub(m: re.Match[str]) -> str:
        val = env.get(m.group(1))
        return val if val else (m.group(2) or "")

    return _VAR.sub(sub, text)


def load_yaml_settings(path: str | Path, environ: dict[str, str] | None = None) -> YamlSettings:
    """Read and validate the YAML settings file."""
    raw: dict[str, Any] = yaml.safe_load(expand_env(Path(path).read_text(encoding="utf-8"), environ))
    return YamlSettings.model_validate(raw)


def _dotenv_values() -> dict[str, str]:
    out: dict[str, str] = {}
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip("\"'").strip()
    return out


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton. Call get_settings.cache_clear() in tests after changing env."""
    env = EnvSettings()
    environ = {**_dotenv_values(), **{k: v for k, v in os.environ.items() if v}}
    return Settings(env=env, cfg=load_yaml_settings(env.settings_file, environ))
