from sqlalchemy import Column, Integer, String, Text, TIMESTAMP, Numeric
from sqlalchemy.sql import func
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class CallLog(Base):
    __tablename__ = 'call_logs'
    id = Column(Integer, primary_key=True)
    driver_id = Column(Integer, nullable=False)
    broker_id = Column(Integer)
    negotiation_id = Column(Integer)
    load_ref = Column(String(40))
    phone = Column(String(40))
    outcome = Column(String(20))
    rate = Column(Numeric(10,2))
    notes = Column(Text)
    next_follow_up_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
