"""
Configuration Management - Enterprise Grade

Features:
- Environment-based configuration
- Type-safe settings
- Secret management
- Feature flags
- Rate limiting config
"""

import os
from typing import Optional, List
from dataclasses import dataclass, field
from enum import Enum


class Environment(str, Enum):
    """Deployment environments"""
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TESTING = "testing"


@dataclass
class DatabaseConfig:
    """Database configuration"""
    url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/llama_agent"
    echo: bool = False  # SQL logging
    pool_size: int = 20
    max_overflow: int = 10
    pool_pre_ping: bool = True

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        return cls(
            url=os.getenv("DATABASE_URL", cls.url),
            echo=os.getenv("DB_ECHO", "false").lower() == "true",
            pool_size=int(os.getenv("DB_POOL_SIZE", "20")),
            max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
        )


@dataclass
class RedisConfig:
    """Redis configuration"""
    url: str = "redis://localhost:6379"
    max_connections: int = 20
    socket_timeout: int = 5

    @classmethod
    def from_env(cls) -> "RedisConfig":
        return cls(
            url=os.getenv("REDIS_URL", cls.url),
            max_connections=int(os.getenv("REDIS_MAX_CONNECTIONS", "20")),
        )


@dataclass
class JWTConfig:
    """JWT configuration"""
    secret_key: str = "your-secret-key-change-in-production"
    algorithm: str = "HS256"
    access_expire_minutes: int = 30
    refresh_expire_days: int = 7

    @classmethod
    def from_env(cls) -> "JWTConfig":
        return cls(
            secret_key=os.getenv("JWT_SECRET_KEY", cls.secret_key),
            algorithm=os.getenv("JWT_ALGORITHM", cls.algorithm),
            access_expire_minutes=int(os.getenv("JWT_EXPIRE_MINUTES", "30")),
            refresh_expire_days=int(os.getenv("JWT_REFRESH_EXPIRE_DAYS", "7")),
        )


@dataclass
class LLMConfig:
    """LLM configuration"""
    provider: str = "groq"
    model: str = "llama-3.3-70b-versatile"
    api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 1024
    timeout: int = 60

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            api_key=os.getenv("GROQ_API_KEY", ""),
            model=os.getenv("LLM_MODEL", cls.model),
            temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("LLM_MAX_TOKENS", "1024")),
        )


@dataclass
class RateLimitConfig:
    """Rate limiting configuration"""
    enabled: bool = True
    requests_per_minute: int = 60
    requests_per_hour: int = 1000
    burst_size: int = 10

    @classmethod
    def from_env(cls) -> "RateLimitConfig":
        return cls(
            enabled=os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true",
            requests_per_minute=int(os.getenv("RATE_LIMIT_RPM", "60")),
            requests_per_hour=int(os.getenv("RATE_LIMIT_RPH", "1000")),
        )


@dataclass
class CORSConfig:
    """CORS configuration"""
    allow_origins: List[str] = field(default_factory=lambda: ["*"])
    allow_credentials: bool = True
    allow_methods: List[str] = field(default_factory=lambda: ["*"])
    allow_headers: List[str] = field(default_factory=lambda: ["*"])

    @classmethod
    def from_env(cls) -> "CORSConfig":
        origins = os.getenv("CORS_ORIGINS", "*")
        return cls(
            allow_origins=origins.split(",") if origins != "*" else ["*"],
        )


@dataclass
class FeatureFlags:
    """Feature flags for gradual rollout"""
    enable_websockets: bool = True
    enable_auth: bool = True
    enable_rate_limiting: bool = True
    enable_metrics: bool = True
    enable_caching: bool = True

    @classmethod
    def from_env(cls) -> "FeatureFlags":
        return cls(
            enable_websockets=os.getenv("FEATURE_WEBSOCKETS", "true").lower() == "true",
            enable_auth=os.getenv("FEATURE_AUTH", "true").lower() == "true",
            enable_rate_limiting=os.getenv("FEATURE_RATE_LIMITING", "true").lower() == "true",
            enable_metrics=os.getenv("FEATURE_METRICS", "true").lower() == "true",
            enable_caching=os.getenv("FEATURE_CACHING", "true").lower() == "true",
        )


@dataclass
class AppConfig:
    """
    Main application configuration
    Aggregates all sub-configurations
    """
    # Environment
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = False
    app_name: str = "Llama Agent"
    version: str = "3.0.0"

    # Sub-configurations
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    jwt: JWTConfig = field(default_factory=JWTConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    cors: CORSConfig = field(default_factory=CORSConfig)
    features: FeatureFlags = field(default_factory=FeatureFlags)

    # Server
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    ws_port: int = 8765
    streamlit_port: int = 8501

    @classmethod
    def from_env(cls) -> "AppConfig":
        """Load configuration from environment variables"""
        env = os.getenv("ENVIRONMENT", "development").lower()
        
        return cls(
            environment=Environment(env),
            debug=os.getenv("DEBUG", "false").lower() == "true",
            database=DatabaseConfig.from_env(),
            redis=RedisConfig.from_env(),
            jwt=JWTConfig.from_env(),
            llm=LLMConfig.from_env(),
            rate_limit=RateLimitConfig.from_env(),
            cors=CORSConfig.from_env(),
            features=FeatureFlags.from_env(),
            api_host=os.getenv("API_HOST", "0.0.0.0"),
            api_port=int(os.getenv("API_PORT", "8000")),
        )

    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION

    def is_development(self) -> bool:
        return self.environment == Environment.DEVELOPMENT


# ============ SINGLETON INSTANCE ============

_config: Optional[AppConfig] = None


def get_config() -> AppConfig:
    """Get or create the configuration singleton"""
    global _config
    if _config is None:
        _config = AppConfig.from_env()
    return _config


def reload_config() -> AppConfig:
    """Force reload configuration from environment"""
    global _config
    _config = AppConfig.from_env()
    return _config
