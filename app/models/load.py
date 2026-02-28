from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.database import Base


class Load(Base):
    __tablename__ = "loads"

    id = Column(Integer, primary_key=True, index=True)
    ref_id = Column(String, unique=True, index=True) # From Scout (e.g. TS-123)
    origin = Column(String, index=True)
    destination = Column(String, index=True)
    mc_number = Column(String(20), index=True, nullable=True)
    source_platform = Column(String(20), index=True, nullable=True)
    price = Column(String) # We store as string first to handle "$" symbols
    equipment_type = Column(String)
    contact_instructions = Column(String(20), nullable=False, default="email")
    broker_match_status = Column(String(30), nullable=True, index=True)  # resolved|unknown_mc|missing_mc|malformed_mc
    load_metadata = Column("metadata", JSONB, nullable=True)
    raw_data = Column(String, nullable=True)
    ingested_by_driver_id = Column(Integer, ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())