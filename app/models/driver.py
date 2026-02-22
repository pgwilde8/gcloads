from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.sql import func

from app.database import Base


class Driver(Base):
	__tablename__ = "drivers"

	id = Column(Integer, primary_key=True, index=True)
	email = Column(String, unique=True, index=True, nullable=False)
	mc_number = Column(String, index=True, nullable=False)
	dot_number = Column(String(20), index=True, nullable=True)
	referred_by_id = Column(Integer, ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True, index=True)
	display_name = Column(String, nullable=False)
	dispatch_handle = Column(String(20), nullable=True, index=True)
	onboarding_status = Column(String(30), nullable=False, default="needs_profile")
	factor_type = Column(String(30), nullable=True)
	factor_packet_email = Column(String(255), nullable=True)
	email_verified_at = Column(DateTime(timezone=True), nullable=True)
	balance = Column(Float, nullable=False, default=1.0)
	referral_started_at = Column(DateTime(timezone=True), nullable=True)
	referral_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
	stripe_customer_id = Column(String(255), nullable=True)
	stripe_default_payment_method_id = Column(String(255), nullable=True)
	stripe_payment_status = Column(String(40), nullable=False, default="UNSET")
	stripe_action_required = Column(Boolean, nullable=False, default=False)
	min_cpm = Column(Float, nullable=True)
	min_flat_rate = Column(Float, nullable=True)
	auto_negotiate = Column(Boolean, nullable=False, default=True)
	review_before_send = Column(Boolean, nullable=False, default=False)
	billing_state = Column(String(40), nullable=False, default="active")
	preferred_origin_region = Column(String(100), nullable=True)
	preferred_destination_region = Column(String(100), nullable=True)
	preferred_equipment_type = Column(String(100), nullable=True)
	scout_active = Column(Boolean, nullable=False, default=False)
	auto_send_on_perfect_match = Column(Boolean, nullable=False, default=False)
	approval_threshold = Column(Integer, nullable=False, default=3)
	scout_api_key = Column(String(64), unique=True, nullable=True)
	created_at = Column(DateTime(timezone=True), server_default=func.now())
	updated_at = Column(
		DateTime(timezone=True),
		server_default=func.now(),
		onupdate=func.now(),
	)
