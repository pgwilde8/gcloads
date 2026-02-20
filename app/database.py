from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    db_user: str = "postgres"
    db_password: str = "postgres"
    db_name: str = "postgres"
    db_host: str = "db"
    db_port: int = 5432
    database_url: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            "postgresql+psycopg2://"
            f"{self.db_user}:{self.db_password}@"
            f"{self.db_host}:{self.db_port}/{self.db_name}"
        )


db_settings = DatabaseSettings()
DATABASE_URL = db_settings.resolved_database_url

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database_connection() -> bool:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return True