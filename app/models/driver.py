from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.sql import func

from app.database import Base


class Driver(Base):
	__tablename__ = "drivers"

	id = Column(Integer, primary_key=True, index=True)
	email = Column(String, unique=True, index=True, nullable=False)
	mc_number = Column(String, index=True, nullable=False)
	referred_by_id = Column(Integer, ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True, index=True)
	display_name = Column(String, nullable=False)
	balance = Column(Float, nullable=False, default=1.0)
	min_cpm = Column(Float, nullable=True)
	min_flat_rate = Column(Float, nullable=True)
	auto_negotiate = Column(Boolean, nullable=False, default=True)
	review_before_send = Column(Boolean, nullable=False, default=False)
	created_at = Column(DateTime(timezone=True), server_default=func.now())
	updated_at = Column(
		DateTime(timezone=True),
		server_default=func.now(),
		onupdate=func.now(),
	)
