from datetime import datetime
from sqlalchemy.exc import IntegrityError
from app.services.email import send_factoring_packet_email
from app.core.config import settings as _core_settings
MAX_EMAIL_ATTACHMENT_BYTES = _core_settings.MAX_EMAIL_ATTACHMENT_BYTES
from app.services.storage import get_object
from app.models.driver import Driver
from app.models.negotiation import Negotiation
from app.models.driver_documents import DriverDocument
from app.models.factoring_submissions import FactoringSubmission

REQUIRED_DOCS = ['W9', 'INSURANCE', 'AUTHORITY']

async def send_to_factoring(db, driver_id, negotiation_id, force=False):
    driver = db.query(Driver).filter(Driver.id == driver_id).first()
    if not driver:
        return {'ok': False, 'status': 'error', 'message': 'Driver not found'}
    if driver.onboarding_status != 'active':
        return {'ok': False, 'status': 'blocked', 'message': 'Account not active yet', 'redirect_url': '/onboarding/pending-century'}
    if not driver.factor_packet_email:
        return {'ok': False, 'status': 'blocked', 'message': 'Missing factoring email', 'redirect_url': '/onboarding/factoring'}

    docs = db.query(DriverDocument).filter(DriverDocument.driver_id == driver_id, DriverDocument.is_active == True).all()
    doc_types = {d.doc_type for d in docs}
    missing_docs = [d for d in REQUIRED_DOCS if d not in doc_types]
    if missing_docs:
        return {'ok': False, 'status': 'blocked', 'message': f'Missing docs: {missing_docs}', 'redirect_url': '/onboarding/step3'}

    bol_doc = db.query(DriverDocument).filter(DriverDocument.negotiation_id == negotiation_id, DriverDocument.doc_type.in_(['BOL_RAW', 'BOL_PACKET']), DriverDocument.is_active == True).first()
    if not bol_doc:
        return {'ok': False, 'status': 'blocked', 'message': 'Missing BOL document', 'redirect_url': '/paperwork'}

    packet_doc = db.query(DriverDocument).filter(DriverDocument.negotiation_id == negotiation_id, DriverDocument.doc_type == 'NEGOTIATION_PACKET', DriverDocument.is_active == True).first()
    if not packet_doc or force:
        # Compose full packet (call compose logic/service)
        # This is a placeholder; actual compose logic should be called here
        packet_doc = compose_full_packet(db, driver_id, negotiation_id)
        if not packet_doc:
            return {'ok': False, 'status': 'blocked', 'message': 'Failed to compose full packet', 'redirect_url': '/paperwork'}

    # Size guard
    packet_bytes = get_object(packet_doc.bucket, packet_doc.file_key)
    if len(packet_bytes) > MAX_EMAIL_ATTACHMENT_BYTES:
        return {'ok': False, 'status': 'blocked', 'message': 'Packet too large to send', 'code': 413}

    # Upsert submission
    submission = db.query(FactoringSubmission).filter(FactoringSubmission.negotiation_id == negotiation_id).first()
    if submission:
        if submission.status == 'SENT' and not force:
            return {'ok': True, 'status': 'already_sent', 'to_email': submission.to_email, 'packet_key': submission.packet_key, 'sent_at': submission.sent_at, 'submission_id': submission.id, 'message': 'Already sent'}
        # Update for retry
        submission.packet_key = packet_doc.file_key
        submission.packet_bucket = packet_doc.bucket
        submission.status = 'QUEUED'
        submission.updated_at = datetime.utcnow()
        db.commit()
    else:
        submission = FactoringSubmission(
            negotiation_id=negotiation_id,
            driver_id=driver_id,
            to_email=driver.factor_packet_email,
            packet_doc_type='NEGOTIATION_PACKET',
            packet_bucket=packet_doc.bucket,
            packet_key=packet_doc.file_key,
            status='QUEUED',
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(submission)
        db.commit()
        db.refresh(submission)

    # Send email
    subject = f"Factoring Packet – Negotiation {negotiation_id}"
    body = f"Driver: {driver.name}\nMC: {driver.mc_number}\nDOT: {driver.dot_number}\nPacket attached."
    try:
        send_factoring_packet_email(driver.factor_packet_email, subject, body, [("FactoringPacket.pdf", packet_bytes)])
        submission.status = 'SENT'
        submission.sent_at = datetime.utcnow()
        submission.error_message = None
    except Exception as e:
        submission.status = 'FAILED'
        submission.error_message = str(e)
    submission.updated_at = datetime.utcnow()
    db.commit()

    return {'ok': submission.status == 'SENT', 'status': submission.status, 'to_email': submission.to_email, 'packet_key': submission.packet_key, 'sent_at': submission.sent_at, 'submission_id': submission.id, 'message': submission.error_message}

# Placeholder for packet compose logic
def compose_full_packet(db, driver_id, negotiation_id):
    # Should call actual packet_compose service
    return db.query(DriverDocument).filter(DriverDocument.negotiation_id == negotiation_id, DriverDocument.doc_type == 'NEGOTIATION_PACKET', DriverDocument.is_active == True).first()
