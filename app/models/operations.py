from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


class Negotiation(Base):
    __tablename__ = "negotiations"

    id = Column(Integer, primary_key=True, index=True)
    load_id = Column(Integer, ForeignKey("loads.id", ondelete="CASCADE"), nullable=False, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False, index=True)
    broker_mc_number = Column(String(20), ForeignKey("webwise.brokers.mc_number"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="Draft", index=True)
    current_offer = Column(Numeric(12, 2))
    rate_con_path = Column(String(1024), nullable=True)
    pending_review_subject = Column(String(255), nullable=True)
    pending_review_body = Column(Text, nullable=True)
    pending_review_action = Column(String(40), nullable=True)
    pending_review_price = Column(Numeric(12, 2), nullable=True)
    factoring_status = Column(String(20), nullable=True)
    factored_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    negotiation_id = Column(Integer, ForeignKey("negotiations.id", ondelete="CASCADE"), nullable=False, index=True)
    sender = Column(String(20), nullable=False, index=True)
    body = Column(Text, nullable=False)
    is_read = Column(Boolean, nullable=False, default=False, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False, index=True)
    amount = Column(Numeric(18, 6), nullable=False)
    type = Column(String(20), nullable=False, index=True)
    currency = Column(String(10), nullable=False, index=True)
    stripe_transfer_id = Column(String(255), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class LoadDocument(Base):
    __tablename__ = "load_documents"

    id = Column(Integer, primary_key=True, index=True)
    load_id = Column(Integer, ForeignKey("loads.id", ondelete="CASCADE"), nullable=False, index=True)
    document_type = Column(String(20), nullable=False, index=True)
    s3_key = Column(String(512), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class ScoutStatus(Base):
    __tablename__ = "scout_status"

    driver_id = Column(Integer, ForeignKey("drivers.id", ondelete="CASCADE"), primary_key=True)
    last_ping = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    active_tab = Column(String(20), nullable=True)


class BrokerOverride(Base):
    __tablename__ = "broker_overrides"
    __table_args__ = (
        UniqueConstraint("driver_id", "broker_mc_number", name="uq_broker_overrides_driver_broker"),
    )

    id = Column(Integer, primary_key=True, index=True)
    driver_id = Column(Integer, ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False, index=True)
    broker_mc_number = Column(String(20), ForeignKey("webwise.brokers.mc_number"), nullable=False, index=True)
    rating = Column(Integer, nullable=True)
    notes = Column(Text, nullable=True)
    is_blocked = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
