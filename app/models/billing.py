from sqlalchemy import Column, Date, Integer, Numeric, String, Text, TIMESTAMP, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


class DriverInvoice(Base):
    __tablename__ = "driver_invoices"

    id                       = Column(Integer, primary_key=True)
    driver_id                = Column(Integer, ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False, index=True)
    negotiation_id           = Column(Integer, ForeignKey("negotiations.id", ondelete="CASCADE"), nullable=False)
    gross_amount_cents       = Column(Integer, nullable=False)
    fee_rate                 = Column(Numeric(6, 4), nullable=False, default="0.0250")
    fee_amount_cents         = Column(Integer, nullable=False)
    status                   = Column(String(40), nullable=False, default="pending", index=True)
    billed_week_ending       = Column(Date, nullable=True)
    stripe_payment_intent_id = Column(String(255), nullable=True)
    created_at               = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    paid_at                  = Column(TIMESTAMP(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("negotiation_id", name="uq_driver_invoices_negotiation"),
    )


class BillingRun(Base):
    __tablename__ = "billing_runs"

    id                       = Column(Integer, primary_key=True)
    driver_id                = Column(Integer, ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False, index=True)
    week_ending              = Column(Date, nullable=False)
    status                   = Column(String(40), nullable=False, default="pending", index=True)
    total_amount_cents       = Column(Integer, nullable=False, default=0)
    stripe_payment_intent_id = Column(String(255), nullable=True)
    error_message            = Column(Text, nullable=True)
    created_at               = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False)
    updated_at               = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("driver_id", "week_ending", name="uq_billing_runs_driver_week"),
    )


class BillingRunItem(Base):
    __tablename__ = "billing_run_items"

    id                = Column(Integer, primary_key=True)
    billing_run_id    = Column(Integer, ForeignKey("billing_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    driver_invoice_id = Column(Integer, ForeignKey("driver_invoices.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (
        UniqueConstraint("driver_invoice_id", name="uq_billing_run_items_invoice"),
    )
