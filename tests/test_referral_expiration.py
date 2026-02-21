import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.services.ledger import process_load_fees


sqlite3.register_adapter(Decimal, float)


def _build_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE drivers (
                    id INTEGER PRIMARY KEY,
                    referred_by_id INTEGER NULL,
                    referral_started_at TIMESTAMP NULL,
                    referral_expires_at TIMESTAMP NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE fee_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    negotiation_id INTEGER,
                    driver_id INTEGER,
                    total_load_value NUMERIC(12,2),
                    total_fee_collected NUMERIC(10,2),
                    slice_driver_credits NUMERIC(10,2),
                    slice_infra_reserve NUMERIC(10,2),
                    slice_platform_profit NUMERIC(10,2),
                    slice_treasury NUMERIC(10,2),
                    referral_bounty_paid NUMERIC(10,2),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(text("CREATE UNIQUE INDEX uq_fee_ledger_negotiation ON fee_ledger(negotiation_id)"))
        connection.execute(
            text(
                """
                CREATE TABLE referral_earnings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id INTEGER,
                    referred_driver_id INTEGER,
                    negotiation_id INTEGER,
                    amount NUMERIC(10,2),
                    status VARCHAR(20) DEFAULT 'PENDING',
                    payout_type VARCHAR(20) DEFAULT 'CANDLE',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )

    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_referral_valid_within_18_months():
    SessionLocal = _build_session()
    now_utc = datetime.now(timezone.utc)

    with SessionLocal() as db:
        db.execute(text("INSERT INTO drivers (id) VALUES (1)"))
        db.execute(
            text(
                """
                INSERT INTO drivers (id, referred_by_id, referral_started_at, referral_expires_at)
                VALUES (2, 1, :started_at, :expires_at)
                """
            ),
            {
                "started_at": now_utc - timedelta(days=10),
                "expires_at": now_utc + timedelta(days=30),
            },
        )

        result = process_load_fees(db, load_value=1200, driver_id=2, negotiation_id=1001)
        db.commit()

        assert result["created"] is True
        assert result["referral_bounty_paid"] == 3.0

        referral_count = db.execute(
            text("SELECT COUNT(*) AS count_value FROM referral_earnings WHERE negotiation_id = 1001")
        ).scalar_one()
        assert int(referral_count) == 1


def test_referral_stops_after_expiration():
    SessionLocal = _build_session()
    now_utc = datetime.now(timezone.utc)

    with SessionLocal() as db:
        db.execute(text("INSERT INTO drivers (id) VALUES (1)"))
        db.execute(
            text(
                """
                INSERT INTO drivers (id, referred_by_id, referral_started_at, referral_expires_at)
                VALUES (2, 1, :started_at, :expires_at)
                """
            ),
            {
                "started_at": now_utc - timedelta(days=600),
                "expires_at": now_utc - timedelta(days=1),
            },
        )

        result = process_load_fees(db, load_value=4000, driver_id=2, negotiation_id=1002)
        db.commit()

        assert result["created"] is True
        assert result["referral_bounty_paid"] == 0.0

        referral_count = db.execute(
            text("SELECT COUNT(*) AS count_value FROM referral_earnings WHERE negotiation_id = 1002")
        ).scalar_one()
        assert int(referral_count) == 0


def test_non_referred_driver_unchanged():
    SessionLocal = _build_session()

    with SessionLocal() as db:
        db.execute(text("INSERT INTO drivers (id, referred_by_id) VALUES (3, NULL)"))

        result = process_load_fees(db, load_value=2000, driver_id=3, negotiation_id=1003)
        db.commit()

        assert result["created"] is True
        assert result["referral_bounty_paid"] == 0.0

        referral_count = db.execute(
            text("SELECT COUNT(*) AS count_value FROM referral_earnings WHERE negotiation_id = 1003")
        ).scalar_one()
        assert int(referral_count) == 0


def test_idempotency_remains_intact():
    SessionLocal = _build_session()

    with SessionLocal() as db:
        db.execute(text("INSERT INTO drivers (id) VALUES (1)"))
        db.execute(text("INSERT INTO drivers (id, referred_by_id) VALUES (2, 1)"))

        first = process_load_fees(db, load_value=1200, driver_id=2, negotiation_id=1004)
        second = process_load_fees(db, load_value=1200, driver_id=2, negotiation_id=1004)
        db.commit()

        assert first["created"] is True
        assert second["created"] is False

        ledger_count = db.execute(
            text("SELECT COUNT(*) AS count_value FROM fee_ledger WHERE negotiation_id = 1004")
        ).scalar_one()
        assert int(ledger_count) == 1
