from __future__ import annotations

import yaml
from pydantic import BaseModel, model_validator


class RetryConfig(BaseModel):
    max_attempts: int = 3
    max_delay: float = 30.0


class ProviderConfig(BaseModel):
    default: str
    fallback: str | None = None


class ProvidersConfig(BaseModel):
    email: ProviderConfig
    sms: ProviderConfig
    webhook: ProviderConfig


class RuleConfig(BaseModel):
    condition: str | None = None
    default: bool = False
    channels: list[str]
    priority: str = "medium"


class ProfileConfig(BaseModel):
    rules: list[RuleConfig]

    @model_validator(mode="after")
    def must_have_exactly_one_default(self) -> "ProfileConfig":
        defaults = [r for r in self.rules if r.default]
        if len(defaults) != 1:
            raise ValueError(
                f"Profile must have exactly one default rule, got {len(defaults)}"
            )
        if self.rules[-1] not in defaults:
            raise ValueError("The default rule must be last in the rules list")
        return self


class AppConfig(BaseModel):
    providers: ProvidersConfig
    profiles: dict[str, ProfileConfig]
    retry: RetryConfig = RetryConfig()


def load_config(path: str) -> AppConfig:
    with open(path) as f:
        raw = yaml.safe_load(f)
    return AppConfig.model_validate(raw)
