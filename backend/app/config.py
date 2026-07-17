from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    qwen_reasoning_model: str = "qwen3.7-plus"
    qwen_extraction_model: str = "qwen3.6-flash"
    qwen_embedding_model: str = "text-embedding-v4"
    qwen_rerank_model: str = "qwen3-rerank"
    qwen_rerank_url: str = "https://dashscope-intl.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"

    database_url: str = "postgresql+asyncpg://postgres:postgres@db:5432/triagetrace"
    sync_database_url: str = "postgresql://postgres:postgres@db:5432/triagetrace"

    app_env: str = "development"
    log_level: str = "info"
    memory_token_budget: int = 800
    use_llm_poison_check: bool = False
    default_tenant: str = "default"

    live_qwen_test: int = 0


settings = Settings()
