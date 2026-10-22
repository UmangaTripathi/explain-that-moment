from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    azure_openai_endpoint: str = ""
    foundry_project_endpoint: str = ""
    main_model_deployment: str = "gpt-5.4"
    mini_model_deployment: str = "gpt-5.4-mini"
    allowed_origins: str = "http://localhost:5173"
    app_version: str = "dev"

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
