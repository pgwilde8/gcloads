from email.message import EmailMessage
import os
import unittest

from app.logic.parser import extract_negotiation_id_from_message, extract_routing_from_message


class TestEmailRoutingPrecedence(unittest.TestCase):
    def test_recipient_header_precedence_delivered_to_wins(self):
        msg = EmailMessage()
        msg["Delivered-To"] = "dispatch+111@gcdloads.com"
        msg["To"] = "dispatch+222@gcdloads.com"
        msg["Cc"] = "dispatch+333@gcdloads.com"

        routed = extract_routing_from_message(msg, email_domain="gcdloads.com")

        self.assertIsNotNone(routed)
        self.assertEqual(routed["load_ref"], "111")
        self.assertEqual(routed["matched_header"], "Delivered-To")

    def test_negotiation_precedence_plus_tag_beats_subject_and_header(self):
        msg = EmailMessage()
        msg["To"] = "dispatch+444@gcdloads.com"
        msg["Subject"] = "Re: update [GCD:555]"
        msg["X-GCD-Negotiation-ID"] = "666"

        result = extract_negotiation_id_from_message(msg, email_domain="gcdloads.com")

        self.assertIsNotNone(result)
        self.assertEqual(result["negotiation_id"], 444)
        self.assertEqual(result["layer"], "plus_tag")

    def test_negotiation_precedence_subject_beats_x_header_without_plus_tag(self):
        msg = EmailMessage()
        msg["To"] = "broker@somewhere.com"
        msg["Subject"] = "Broker reply [GCD:777]"
        msg["X-GCD-Negotiation-ID"] = "888"

        result = extract_negotiation_id_from_message(msg, email_domain="gcdloads.com")

        self.assertIsNotNone(result)
        self.assertEqual(result["negotiation_id"], 777)
        self.assertEqual(result["layer"], "subject_token")

    def test_reply_to_fallback_when_recipient_headers_missing(self):
        msg = EmailMessage()
        msg["Reply-To"] = "dispatch+999@gcdloads.com"

        routed = extract_routing_from_message(msg, email_domain="gcdloads.com")
        result = extract_negotiation_id_from_message(msg, email_domain="gcdloads.com")

        self.assertIsNotNone(routed)
        self.assertEqual(routed["matched_header"], "Reply-To")
        self.assertIsNotNone(result)
        self.assertEqual(result["negotiation_id"], 999)
        self.assertEqual(result["layer"], "plus_tag")

    def test_subject_token_requires_digits_only(self):
        msg = EmailMessage()
        msg["Subject"] = "FW: docs [GCD:ABC123]"
        msg["X-GCD-Negotiation-ID"] = "12345"

        result = extract_negotiation_id_from_message(msg, email_domain="gcdloads.com")

        self.assertIsNotNone(result)
        self.assertEqual(result["negotiation_id"], 12345)
        self.assertEqual(result["layer"], "x_header")

    def test_cross_domain_plus_tag_is_ignored_and_falls_back(self):
        msg = EmailMessage()
        msg["To"] = "dispatch+321@otherdomain.com"
        msg["Subject"] = "Re: lane update [GCD:654]"
        msg["X-GCD-Negotiation-ID"] = "987"

        routed = extract_routing_from_message(msg, email_domain="gcdloads.com")
        result = extract_negotiation_id_from_message(msg, email_domain="gcdloads.com")

        self.assertIsNone(routed)
        self.assertIsNotNone(result)
        self.assertEqual(result["negotiation_id"], 654)
        self.assertEqual(result["layer"], "subject_token")

    def test_legacy_handle_plus_route_is_allowed(self):
        msg = EmailMessage()
        msg["To"] = "pgwilde+123@gcdloads.com"
        msg["Subject"] = "Re: [GCD:777]"

        result = extract_negotiation_id_from_message(msg, email_domain="gcdloads.com")

        self.assertIsNotNone(result)
        self.assertEqual(result["negotiation_id"], 123)
        self.assertEqual(result["layer"], "plus_tag")

    def test_invalid_local_part_plus_route_is_rejected(self):
        msg = EmailMessage()
        msg["To"] = "sales-team+123@gcdloads.com"
        msg["Subject"] = "Re: [GCD:321]"

        routed = extract_routing_from_message(msg, email_domain="gcdloads.com")
        result = extract_negotiation_id_from_message(msg, email_domain="gcdloads.com")

        self.assertIsNone(routed)
        self.assertIsNotNone(result)
        self.assertEqual(result["negotiation_id"], 321)
        self.assertEqual(result["layer"], "subject_token")

    def test_non_digit_plus_token_falls_back(self):
        msg = EmailMessage()
        msg["To"] = "dispatch+abc@gcdloads.com"
        msg["X-GCD-Negotiation-ID"] = "345"

        routed = extract_routing_from_message(msg, email_domain="gcdloads.com")
        result = extract_negotiation_id_from_message(msg, email_domain="gcdloads.com")

        self.assertIsNotNone(routed)
        self.assertEqual(routed["load_ref"], "abc")
        self.assertIsNotNone(result)
        self.assertEqual(result["negotiation_id"], 345)
        self.assertEqual(result["layer"], "x_header")

    def test_dispatch_only_mode_rejects_handle_local_parts(self):
        previous_mode = os.environ.get("EMAIL_PLUS_LOCAL_MODE")
        os.environ["EMAIL_PLUS_LOCAL_MODE"] = "dispatch_only"
        try:
            msg = EmailMessage()
            msg["To"] = "pgwilde+123@gcdloads.com"
            msg["Subject"] = "Re: [GCD:456]"

            routed = extract_routing_from_message(msg, email_domain="gcdloads.com")
            result = extract_negotiation_id_from_message(msg, email_domain="gcdloads.com")

            self.assertIsNone(routed)
            self.assertIsNotNone(result)
            self.assertEqual(result["negotiation_id"], 456)
            self.assertEqual(result["layer"], "subject_token")
        finally:
            if previous_mode is None:
                os.environ.pop("EMAIL_PLUS_LOCAL_MODE", None)
            else:
                os.environ["EMAIL_PLUS_LOCAL_MODE"] = previous_mode

    def test_dispatch_only_mode_still_accepts_dispatch_local_part(self):
        previous_mode = os.environ.get("EMAIL_PLUS_LOCAL_MODE")
        os.environ["EMAIL_PLUS_LOCAL_MODE"] = "dispatch_only"
        try:
            msg = EmailMessage()
            msg["To"] = "dispatch+123@gcdloads.com"

            result = extract_negotiation_id_from_message(msg, email_domain="gcdloads.com")

            self.assertIsNotNone(result)
            self.assertEqual(result["negotiation_id"], 123)
            self.assertEqual(result["layer"], "plus_tag")
        finally:
            if previous_mode is None:
                os.environ.pop("EMAIL_PLUS_LOCAL_MODE", None)
            else:
                os.environ["EMAIL_PLUS_LOCAL_MODE"] = previous_mode


if __name__ == "__main__":
    unittest.main()
