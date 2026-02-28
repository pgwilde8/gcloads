"""
Single entry point for all factoring submissions.

Usage:
    from app.services.factoring_facade import submit_to_factoring

    result = submit_to_factoring(db, negotiation_id=neg.id, driver_id=driver.id)

Routing logic:
  - If driver.factor_packet_email is set AND FACTORING_API_URL is not configured
    → email path  (factoring_send.send_to_factoring)
  - Otherwise
    → API path    (factoring.send_negotiation_to_factoring)

Both paths return a dict with at minimum:
    {"ok": bool, "status": str, "message": str}

Never import factoring.py or factoring_send.py directly from routes or other
services — always go through this facade so routing stays in one place.
"""
import logging
import os

from sqlalchemy.orm import Session

from app.models.driver import Driver

logger = logging.getLogger(__name__)


def submit_to_factoring(
    db: Session,
    *,
    negotiation_id: int,
    driver_id: int,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """Route a factoring submission to the correct backend.

    Returns a normalised result dict:
        ok          bool
        status      str   (e.g. "sent", "already_sent", "dry_run", "error")
        message     str
        path        str   ("api" | "email")  — which backend was used
    """
    driver = db.query(Driver).filter(Driver.id == driver_id).first()
    if not driver:
        return {"ok": False, "status": "error", "message": "driver_not_found", "path": None}

    from app.services.billing_gate import maybe_flip_trial_expired, require_active
    maybe_flip_trial_expired(driver, db)
    require_active(driver, "submit factoring packets")

    api_url = (os.getenv("FACTORING_API_URL") or "").strip()
    use_email = bool(driver.factor_packet_email) and not api_url

    if use_email:
        logger.info(
            "factoring_facade: email path negotiation=%d driver=%d dry_run=%s",
            negotiation_id, driver_id, dry_run,
        )
        from app.services.factoring_send import send_to_factoring as _send_email
        import asyncio
        try:
            result = asyncio.get_event_loop().run_until_complete(
                _send_email(db, driver_id, negotiation_id, force=force)
            )
        except RuntimeError:
            # Already inside a running event loop (FastAPI async context)
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    asyncio.run,
                    _send_email(db, driver_id, negotiation_id, force=force),
                )
                result = future.result()
        result.setdefault("path", "email")
        return result

    logger.info(
        "factoring_facade: api path negotiation=%d driver=%d dry_run=%s",
        negotiation_id, driver_id, dry_run,
    )
    from app.services.factoring import send_negotiation_to_factoring as _send_api
    result = _send_api(db, negotiation_id=negotiation_id, driver_id=driver_id, dry_run=dry_run)
    result.setdefault("path", "api")
    return result
