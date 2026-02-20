from sqlalchemy import Column, DateTime, Integer, String
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
    load_metadata = Column("metadata", JSONB, nullable=True)
    raw_data = Column(String, nullable=True) # Full scrape for debugging
    created_at = Column(DateTime(timezone=True), server_default=func.now())