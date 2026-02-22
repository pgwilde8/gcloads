from sqlalchemy import Column, Integer, String, Text, TIMESTAMP
from sqlalchemy.sql import func
from sqlalchemy.orm import declarative_base

Base = declarative_base()

class FactoringSubmission(Base):
    __tablename__ = 'factoring_submissions'
    id = Column(Integer, primary_key=True)
    negotiation_id = Column(Integer, nullable=False)
    driver_id = Column(Integer, nullable=False)
    to_email = Column(String(255), nullable=False)
    packet_doc_type = Column(String(40), nullable=False, default='NEGOTIATION_PACKET')
    packet_bucket = Column(String(255), nullable=False)
    packet_key = Column(Text, nullable=False)
    status = Column(String(40), nullable=False, default='QUEUED')
    error_message = Column(Text)
    sent_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now())
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now())
