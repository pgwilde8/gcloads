from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


class Broker(Base):
    __tablename__ = "brokers"
    __table_args__ = {"schema": "webwise"}

    mc_number = Column(String(20), primary_key=True)
    dot_number = Column(String(20), index=True)
    company_name = Column(String(255), index=True)
    dba_name = Column(String(255))
    website = Column(String(255))
    primary_email = Column(String(255), index=True)
    primary_phone = Column(String(50))
    secondary_phone = Column(String(50))
    fax = Column(String(50))
    phy_street = Column(String(255))
    phy_city = Column(String(100))
    phy_state = Column(String(50))
    phy_zip = Column(String(20))
    rating = Column(Numeric(3, 2))
    source = Column(String(50))
    preferred_contact_method = Column(String(20))
    internal_note = Column(Text)
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())


class BrokerEmail(Base):
    __tablename__ = "broker_emails"
    __table_args__ = (
        UniqueConstraint("mc_number", "email", name="uq_webwise_broker_emails_mc_email"),
        {"schema": "webwise"},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    mc_number = Column(String(20), ForeignKey("webwise.brokers.mc_number", ondelete="CASCADE"), nullable=False, index=True)
    email = Column(Text, nullable=False)
    source = Column(Text)
    confidence = Column(Numeric(4, 3))
    evidence = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())