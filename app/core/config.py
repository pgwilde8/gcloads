import logging

from fastapi import Request

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    """Client IP, honoring X-Forwarded-For when from trusted proxy."""
    forwarded = (request.headers.get("x-forwarded-for") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return (request.client.host if request.client else "") or ""


def _resolve_host(request: Request, trusted_proxy_ips: str | None) -> str:
    """
    Resolve Host for request. Use X-Forwarded-Host only when client IP is in trusted list.
    Otherwise use Host header to prevent spoofing.
    """
    client_ip = _client_ip(request)
    trusted = [ip.strip() for ip in (trusted_proxy_ips or "").split(",") if ip.strip()]
    forwarded = (request.headers.get("x-forwarded-host") or "").strip()
    if trusted and client_ip in trusted and forwarded:
        return forwarded.split(",")[0].strip()
    if forwarded and trusted and client_ip not in trusted:
        logger.debug(
            "beta_host: X-Forwarded-Host ignored (client_ip=%s not in TRUSTED_PROXY_IPS)",
            client_ip or "(none)",
        )
    return (request.headers.get("host") or "").strip()


def get_safe_base_url_from_request(
    request: Request,
    *,
    allowed_hosts: frozenset[str] | None = None,
    fallback_base_url: str | None = None,
    trusted_proxy_ips: str | None = None,
) -> str:
    """
    Build base URL from request host for magic links. Uses request host when whitelisted;
    falls back to APP_BASE_URL otherwise (prevents spoofing links to evil.com).
    Honors X-Forwarded-Host and X-Forwarded-Proto only when client IP is trusted.
    """
    allowed = allowed_hosts or _get_allowed_base_hosts()
    fallback = (fallback_base_url or "").rstrip("/")
    if not fallback:
        fallback = "https://app.greencandledispatch.com"

    client_ip = _client_ip(request)
    trusted = [ip.strip() for ip in (trusted_proxy_ips or "").split(",") if ip.strip()]

    host_raw = _resolve_host(request, trusted_proxy_ips)
    host = (host_raw.split(":")[0] or "").strip().lower()
    if not host:
        return fallback
    if host not in allowed:
        return fallback

    if trusted and client_ip in trusted:
        proto = (request.headers.get("x-forwarded-proto") or "").strip().lower()
        if proto in ("http", "https"):
            scheme = proto
        else:
            scheme = getattr(request.url, "scheme", "https") or "https"
    else:
        scheme = getattr(request.url, "scheme", "https") or "https"

    return f"{scheme}://{host}"


def is_beta_request(request: Request, beta_hosts: str | None = None, trusted_proxy_ips: str | None = None) -> bool:
    """
    Check if the request comes from a beta host (e.g. beta.codriverfreight.com).
    Host is normalized: strip port, lowercase. X-Forwarded-Host used only when client
    IP is in trusted_proxy_ips (prevents spoofing).
    """
    hosts_str = (beta_hosts or "").strip()
    if not hosts_str:
        return False
    host_raw = _resolve_host(request, trusted_proxy_ips)
    if not host_raw:
        return False
    host = host_raw.split(":")[0].strip().lower()
    allowed = [h.strip().lower() for h in hosts_str.split(",") if h.strip()]
    return host in allowed


class CoreSettings(BaseSettings):
    ENV: str = Field(default="development", validation_alias="APP_ENV")
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

    MAX_EMAIL_ATTACHMENT_BYTES: int = 20 * 1024 * 1024  # 20MB default

    APP_BASE_URL: str = "https://app.greencandledispatch.com"
    BETA_HOSTS: str = "beta.codriverfreight.com"
    TRUSTED_PROXY_IPS: str = ""  # Comma-separated IPs; X-Forwarded-Host honored only from these
    CENTURY_REFERRAL_TO_EMAIL: str = "techsmartmarketing8@gmail.com"
    MAGIC_LINK_TOKEN_TTL_MINUTES: int = 30
    MAGIC_LINK_SEND_WINDOW_MINUTES: int = 15
    MAGIC_LINK_SEND_EMAIL_LIMIT: int = 5
    MAGIC_LINK_SEND_IP_LIMIT: int = 20
    ADMIN_TOKEN: str = ""
    ALLOWED_BASE_HOSTS: str = "app.greencandledispatch.com,beta.codriverfreight.com,localhost,127.0.0.1"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = CoreSettings()


def _get_allowed_base_hosts() -> frozenset[str]:
    return frozenset(h.strip().lower() for h in settings.ALLOWED_BASE_HOSTS.split(",") if h.strip())
