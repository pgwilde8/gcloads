import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from typing import Optional, Dict
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# One source of truth for all email variables
# Support both MXROUTE_* and EMAIL_* variable names (EMAIL_* takes precedence if both exist)
MX_HOST = os.getenv("EMAIL_HOST") or os.getenv("MXROUTE_SMTP_HOST", "fusion.mxrouting.net")
MX_PORT = int(os.getenv("EMAIL_PORT") or os.getenv("MXROUTE_SMTP_PORT", "465"))
MX_USER = os.getenv("EMAIL_USER") or os.getenv("MXROUTE_SMTP_USER")
MX_PASS = os.getenv("EMAIL_PASS") or os.getenv("MXROUTE_SMTP_PASSWORD")
EMAIL_DOMAIN = os.getenv("EMAIL_DOMAIN", "gcdloads.com")
MXROUTE_FROM_EMAIL = os.getenv("MXROUTE_FROM_EMAIL") or os.getenv("EMAIL_USER") or "dispatch@gcdloads.com"

# Alias the variables if you need the older names for compatibility
MXROUTE_SMTP_HOST = MX_HOST
MXROUTE_SMTP_PORT = MX_PORT
MXROUTE_SMTP_USER = MX_USER
MXROUTE_SMTP_PASSWORD = MX_PASS

def send_negotiation_email(
    to_email: str,
    subject: str,
    body: str,
    load_id: str,
    negotiation_id: int,
    driver_name: str,
    load_source: Optional[str] = None,
    truck_number: Optional[str] = None,
) -> Dict[str, any]:
    """
    Sends email from [driver]+[load_id]@gcdloads.com via Fusion SMTP.
    If truck_number is set (fleet), appends professional fleet line to body.
    """
    if not MXROUTE_SMTP_USER or not MXROUTE_SMTP_PASSWORD:
        return {"status": "error", "message": "SMTP credentials missing in .env"}

    try:
        # 1. Create the Dynamic Sender (e.g., seth+L123@gcdloads.com)
        # This is the "Magic" that makes replies route to your DB correctly
        sender_email = f"{driver_name.lower()}+{load_id}@{EMAIL_DOMAIN}"
        
        # 2. Add load board tag to broker email (e.g., broker+trucksmarter@domain.com)
        tagged_broker_email = add_load_board_tag(to_email, load_source)

        msg = MIMEMultipart()
        msg['From'] = sender_email
        msg['To'] = tagged_broker_email
        msg['Subject'] = subject
        # Critical: Reply-To ensures even if they hit "Reply", it goes to the tagged address
        msg['Reply-To'] = sender_email 

        # Body with context for the broker
        full_body = body
        if truck_number and str(truck_number).strip():
            full_body += f"\n\nOur driver in Truck #{truck_number.strip()} is ready to pick this up."
        full_body += f"\n\n---\nRef: {load_id}"
        msg.attach(MIMEText(full_body, 'plain'))

        # 3. Connect via SSL to Fusion
        with smtplib.SMTP_SSL(MXROUTE_SMTP_HOST, MXROUTE_SMTP_PORT) as server:
            server.login(MXROUTE_SMTP_USER, MXROUTE_SMTP_PASSWORD)
            server.send_message(msg)

        return {
            "status": "success",
            "sent_from": sender_email,
            "sent_to": tagged_broker_email,
            "sent_at": datetime.now().isoformat()
        }

    except Exception as e:
        return {"status": "error", "message": f"SMTP Error: {str(e)}"}


def add_load_board_tag(email: str, load_source: Optional[str] = None) -> str:
    """Add plus-addressing tag to broker email (e.g. broker+trucksmarter@domain.com)."""
    if not load_source:
        if "+" in email and "@" in email:
            local, domain = email.rsplit("@", 1)
            base_local = local.split("+", 1)[0]
            return f"{base_local}@{domain}"
        return email
    tag = re.sub(r"[^a-z0-9]", "", load_source.lower())
    if "+" in email and "@" in email:
        local, domain = email.rsplit("@", 1)
        base_local = local.split("+", 1)[0]
    elif "@" in email:
        base_local, domain = email.rsplit("@", 1)
    else:
        return email
    return f"{base_local}+{tag}@{domain}"


def parse_broker_reply(email_body: str, email_subject: str = "") -> Dict:
    """
    Parse broker reply to determine status hint (replied/won/lost) and optional extracted rate.
    """
    text = (email_body or "").lower() + " " + (email_subject or "").lower()
    status_hint = "replied"
    extracted_rate = None

    # Look for won/yes/accept indicators
    if any(phrase in text for phrase in ["accepted", "yes we can", "book it", "confirmed", "you got it", "$"]):
        status_hint = "won"
        # Try to extract rate: $1,234 or $1234.56
        rate_match = re.search(r'\$\s*([\d,]+(?:\.\d{2})?)', text)
        if rate_match:
            try:
                extracted_rate = float(rate_match.group(1).replace(",", ""))
            except (ValueError, TypeError):
                pass

    # Look for lost/no indicators
    if any(phrase in text for phrase in ["no thanks", "already covered", "filled", "sorry we", "passed", "can't", "cannot"]):
        status_hint = "lost"

    return {"status_hint": status_hint, "extracted_rate": extracted_rate}


def send_contact_form_email(name: str, user_email: str, message: str) -> Dict:
    """
    Sends contact form submission to CONTACT_RECIPIENT_EMAIL.
    Uses same SMTP as broker emails (MXROUTE_* or EMAIL_*). Reply-To = submitter's email.
    """
    to_email = os.getenv("CONTACT_RECIPIENT_EMAIL", "contact@gcdloads.com")
    user = MX_USER or os.getenv("EMAIL_USER")
    password = MX_PASS or os.getenv("EMAIL_PASS")
    host = MX_HOST or os.getenv("EMAIL_HOST", "fusion.mxrouting.net")
    port = MX_PORT or int(os.getenv("EMAIL_PORT", "465"))
    if not user or not password:
        return {"status": "error", "message": "SMTP credentials missing in .env"}

    try:
        from_email = os.getenv("CONTACT_FROM_EMAIL", f"contact@{EMAIL_DOMAIN}")
        msg = MIMEMultipart()
        msg["From"] = f"Green Candle Contact <{from_email}>"
        msg["To"] = to_email
        msg["Reply-To"] = user_email
        msg["Subject"] = f"Contact Form: {name or 'Unknown'}"

        body = f"""New contact form submission from greencandledispatch.com

Name: {name or 'Not provided'}
Email: {user_email}

Message:
{message or 'No message provided'}

---
Sent at {datetime.now().isoformat()}
"""
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL(host, port) as server:
            server.login(user, password)
            server.send_message(msg)

        return {"status": "success", "sent_to": to_email}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def send_factoring_referral_email(referral_data: dict, to_email: str = "techsmartmarketing8@gmail.com", cc_email: Optional[str] = None) -> Dict:
    """Send factoring referral form to Alma at Century Finance. Referral code: GREEN CANDLE."""
    user = MX_USER or os.getenv("EMAIL_USER")
    password = MX_PASS or os.getenv("EMAIL_PASS")
    host = MX_HOST or os.getenv("EMAIL_HOST", "fusion.mxrouting.net")
    port = MX_PORT or int(os.getenv("EMAIL_PORT", "465"))
    if not user or not password:
        return {"status": "error", "message": "SMTP credentials missing"}
    from_email = os.getenv("FACTORING_FROM_EMAIL", f"referrals@{EMAIL_DOMAIN}")
    mc = referral_data.get("mc_number", "N/A")
    code = referral_data.get("referral_code", "GREEN CANDLE")
    fuel = "Yes" if referral_data.get("interested_fuel_card") else "No"
    body = f"""Hi Alma,

New referral from Green Candle Dispatch (Referral Code: {code}).

Driver Details:
- Full Name: {referral_data.get('full_name', '')}
- Email: {referral_data.get('email', '')}
- Cell Phone: {referral_data.get('cell_phone', '')}
- Secondary #: {referral_data.get('secondary_phone') or 'Not provided'}
- Company Name: {referral_data.get('company_name') or 'Solo owner-operator'}
- MC #: {mc}
- Number of Trucks: {referral_data.get('number_of_trucks', '')}
- Interested in Fuel Card Discounts: {fuel}
- Est. Monthly Volume: {referral_data.get('estimated_monthly_volume') or 'Not specified'}
- Current Factoring: {referral_data.get('current_factoring_company') or 'None'}
- Preferred Funding: {referral_data.get('preferred_funding_speed') or 'Not specified'}

Thanks!
Green Candle Dispatch
"""
    try:
        msg = MIMEMultipart()
        msg["From"] = f"Green Candle Dispatch <{from_email}>"
        msg["To"] = to_email
        if cc_email:
            msg["Cc"] = cc_email
        msg["Subject"] = f"New Green Candle Dispatch Referral - MC# {mc}"
        msg["Reply-To"] = referral_data.get("email", from_email)
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL(host, port) as server:
            server.login(user, password)
            recipients = [to_email] + ([cc_email] if cc_email else [])
            server.send_message(msg, to_addrs=recipients)
        return {"status": "success", "sent_to": to_email}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def send_century_approval_email(driver_name: str, driver_email: str) -> Dict:
    """Send welcome email when Century Finance approves driver. Uses existing login (no password in email)."""
    if not MX_USER or not MX_PASS:
        return {"status": "error", "message": "SMTP credentials missing"}
    from_email = os.getenv("FACTORING_FROM_EMAIL", f"referrals@{EMAIL_DOMAIN}")
    base_url = (os.getenv("BASE_URL") or "https://greencandledispatch.com").rstrip("/")
    login_url = f"{base_url}/login/client"
    dashboard_url = f"{base_url}/drivers/dashboard"
    body = f"""Hi {driver_name},

Century Finance has approved you for funding. Your Green Candle Dispatch back office is now unlocked.

Log in with the same email and password you used when you registered:

{login_url}

After login you'll land on your dashboard. From there you can:
• Open the Load Board to find and negotiate loads
• When you WIN a load, go to Paperwork (or use "Manage" on the load) to upload your BOL, create the invoice, and send the packet to Century for funding

Dashboard: {dashboard_url}

If you have questions, reply here or reach Alma at Century directly.

Welcome aboard!
Green Candle Dispatch
"""
    try:
        msg = MIMEMultipart()
        msg["From"] = f"Green Candle Dispatch <{from_email}>"
        msg["To"] = driver_email
        msg["Subject"] = "You're approved! Log in to your Green Candle Dispatch dashboard"
        msg["Reply-To"] = from_email
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL(MX_HOST, MX_PORT) as server:
            server.login(MX_USER, MX_PASS)
            server.send_message(msg, to_addrs=[driver_email])
        return {"status": "success", "sent_to": driver_email}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def send_century_decline_email(driver_name: str, driver_email: str, refund_info: Optional[str] = None) -> Dict:
    """Send decline email with refund info when Century Finance declines driver."""
    if not MX_USER or not MX_PASS:
        return {"status": "error", "message": "SMTP credentials missing"}
    from_email = os.getenv("FACTORING_FROM_EMAIL", f"referrals@{EMAIL_DOMAIN}")
    refund_text = refund_info or "We've processed your full $25 setup fee refund via Stripe (should appear in 3–10 business days). Check your Stripe receipt or bank statement."
    body = f"""Hi {driver_name},

Alma reviewed your info, but unfortunately funding approval didn't go through at this time.

{refund_text}

If you'd like to try again later or have questions, feel free to reply.

Thanks for giving us a shot — we appreciate it.
Green Candle Dispatch
"""
    try:
        msg = MIMEMultipart()
        msg["From"] = f"Green Candle Dispatch <{from_email}>"
        msg["To"] = driver_email
        msg["Subject"] = "Update from Century Finance – Next Steps"
        msg["Reply-To"] = from_email
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL(MX_HOST, MX_PORT) as server:
            server.login(MX_USER, MX_PASS)
            server.send_message(msg, to_addrs=[driver_email])
        return {"status": "success", "sent_to": driver_email}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def send_bol_email(
    to_email: str,
    bucket: str,
    file_key: str,
    mc_number: str,
    load_id: str,
    subject: Optional[str] = None,
) -> Dict:
    """
    Send BOL PDF as email attachment. Downloads from Spaces, attaches PDF, sends via SMTP.
    
    Args:
        to_email: Recipient email address
        bucket: Spaces bucket name
        file_key: Spaces key (path) to the BOL PDF
        mc_number: MC number for email context
        load_id: Load ID for email context
        subject: Optional custom subject (defaults to auto-generated)
    
    Returns:
        Dict with status and message
    """
    from app.services.storage import get_object
    
    if not MX_USER or not MX_PASS:
        return {"status": "error", "message": "SMTP credentials missing (check EMAIL_USER and EMAIL_PASS in .env)"}
    
    try:
        # Download PDF from Spaces
        pdf_content = get_object(bucket, file_key)
        
        # Create email
        msg = MIMEMultipart()
        msg['From'] = MXROUTE_FROM_EMAIL
        msg['To'] = to_email
        
        if not subject:
            subject = f"BOL Upload - Load {load_id} (MC: {mc_number})"
        msg['Subject'] = subject
        
        # Email body
        body = f"""New Bill of Lading uploaded.

Load ID: {load_id}
MC Number: {mc_number}
Bucket: {bucket}
Key: {file_key}

The BOL PDF is attached.
"""
        msg.attach(MIMEText(body, 'plain'))
        
        # Attach PDF
        filename = file_key.split('/')[-1]  # Get just the filename
        part = MIMEBase('application', 'pdf')
        part.set_payload(pdf_content)
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename="{filename}"')
        msg.attach(part)
        
        # Send email
        with smtplib.SMTP_SSL(MX_HOST, MX_PORT) as server:
            server.login(MX_USER, MX_PASS)
            server.send_message(msg, to_addrs=[to_email])
        
        return {
            "status": "success",
            "message": f"BOL email sent to {to_email}",
            "sent_at": datetime.now().isoformat()
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"status": "error", "message": f"Failed to send BOL email: {str(e)}"}