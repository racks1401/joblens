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
    database_url: str = "postgresql+psycopg://joblens:joblens@localhost:5432/joblens"
    admin_api_key: str = ""
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


class PromptsCfg(BaseModel):
    facts: str = "facts_v1"
    profile: str = "profile_v1"
    explain: str = "explain_v1"
    judge: str = "judge_v1"
    agent: str = "agent_v1"


class LLMCfg(BaseModel):
    timeout_s: float = 60
    max_retries: int = 2
    max_input_tokens: int = 6000


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
    daily_tokens: int = 300000


class IngestCfg(BaseModel):
    requests_per_second: float = 2
    timeout_s: float = 15
    retries: int = 3
    user_agent: str = "JobLens/0.1"
    max_description_chars: int = 20000
    repost_window_days: int = 90
    max_jobs_per_company: int = 0
    india_keywords: list[str] = []
    remote_keywords: list[str] = []
    remote_exclude: list[str] = []


class YamlSettings(BaseModel):
    """Typed view of config/settings.yaml."""

    models: ModelsCfg
    prompts: PromptsCfg = PromptsCfg()
    llm: LLMCfg = LLMCfg()
    embeddings: EmbeddingsCfg
    search: SearchCfg = SearchCfg()
    ranking: RankingCfg = RankingCfg()
    trust: TrustCfg
    agent: AgentCfg = AgentCfg()
    budget: BudgetCfg = BudgetCfg()
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
