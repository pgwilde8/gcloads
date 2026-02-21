from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreSettings(BaseSettings):
    WATERMARK_ENABLED: bool = True
    EMAIL_PLUS_LOCAL_MODE: str = "dispatch_and_handles"

    DISPATCH_FEE_RATE: float = 0.025
    REFERRAL_BOUNTY_RATE: float = 0.10
    REFERRAL_BOUNTY_CAP: float = 5.00

    SLICE_DRIVER_CREDITS_RATE: float = 0.2105
    SLICE_INFRA_RESERVE_RATE: float = 0.2105
    SLICE_PLATFORM_PROFIT_RATE: float = 0.3158
    SLICE_TREASURY_RATE: float = 0.2632

    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""

    FACTORING_API_URL: str = ""
    FACTORING_API_KEY: str = ""
    FACTORING_API_AUTH_HEADER: str = "Authorization"
    FACTORING_API_AUTH_SCHEME: str = "Bearer"
    FACTORING_API_TIMEOUT_SECONDS: int = 20

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = CoreSettings()
