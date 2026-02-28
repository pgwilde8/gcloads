"""
DEPRECATED — do not add new callers.

broker_service.get_broker_contact() is a legacy shim kept only for backward
compatibility.  All broker resolution must go through broker_intelligence:

    from app.services.broker_intelligence import triage_broker_contact

triage_broker_contact() applies MC normalization, padding-aware lookup,
standing checks, and returns a typed action dict.  get_broker_contact()
bypasses all of that.
"""
import warnings

from sqlalchemy.orm import Session

from app.services.broker_intelligence import normalize_mc
from app.models.broker import Broker


def get_broker_contact(db: Session, mc_number: str):
    warnings.warn(
        "broker_service.get_broker_contact() is deprecated. "
        "Use broker_intelligence.triage_broker_contact() instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    clean = normalize_mc(mc_number)
    if not clean:
        return None
    return db.query(Broker).filter(Broker.mc_number == clean).first()
