from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreSettings(BaseSettings):
    WATERMARK_ENABLED: bool = True

    DISPATCH_FEE_RATE: float = 0.025
    REFERRAL_BOUNTY_RATE: float = 0.10
    REFERRAL_BOUNTY_CAP: float = 5.00

    SLICE_DRIVER_CREDITS_RATE: float = 0.2105
    SLICE_INFRA_RESERVE_RATE: float = 0.2105
    SLICE_PLATFORM_PROFIT_RATE: float = 0.3158
    SLICE_TREASURY_RATE: float = 0.2632

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = CoreSettings()
