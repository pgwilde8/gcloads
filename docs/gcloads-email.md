# Dispatch Email Setup: pgwilde@gcdloads.com and Driver Emails

## Strategy: Catch-All (No Per-Address Creation)

**gcdloads.com uses a catch-all** — all *@gcdloads.com deliver to `dispatch@gcdloads.com`. No need to create aliases per driver.

---

## What Happens When a Driver Claims a Handle

`onboarding_claim_handle` (Step 1) does **only**:

1. **Database** — INSERT or UPDATE `trucker_profiles` with `display_name = "pgwilde"`
2. **Next step** — Returns `onboarding_step2.html` (MC/DOT form)

No email-provider API calls. No alias creation.

---

## How Emails Work

| Flow | How |
|------|-----|
| **Outbound** | Send FROM `example+load123@gcdloads.com` via MXRoute SMTP |
| **Inbound** | Brokers reply to `example+load123@gcdloads.com` → catch-all delivers to `dispatch@gcdloads.com` → `inbound_listener.py` reads it |

---

## Do Not Implement

- DirectAdmin API
- Per-driver alias creation

Rely purely on catch-all.

===================================
Here is the breakdown of how the broker gets that address and why the system is more robust than it looks.1. How does the broker get that email?The broker isn't typing webwise5+10245 by hand. They are simply clicking "Reply".Your system is designed to send the outbound solicitation first. When your "Scout" or Joe (the dispatcher) finds a load and clicks "Email Broker," the system sends the email using a Reply-To header or a From address specifically crafted for that deal:From: Joe <webwise5+10245@gcdloads.com>When the broker sees the email in their inbox, they just hit reply. Their email client automatically sends the response back to that "plus-tagged" address. This is how you "track" thousands of conversations without them getting tangled.2. What if they don't include the Load #?It doesn't matter. This is the beauty of the Plus-Tagging (+10245) system.Most AI email parsers try to read the "Body" of the email to find a load number, but brokers are messy—they forget the number, they typo it, or they call it "The NJ run."The Old Way: Search the email body for "10245". (High failure rate).The Green Candle Way: The database doesn't care about the body or the subject line. It looks at the To: address.Because the email was sent to ...+10245@..., the inbound listener knows instantly: "This is for Negotiation ID 10245." Even if the broker replies with just one word ("No"), the system knows exactly which load they are saying "No" to.3. The "Subject Line" Safety NetIn your test email, the subject was Load #10243. Notice that the number in the subject (10243) didn't match our Load ID (10245).The system didn't care. It ignored the subject line and trusted the Plus-Tag in the email address. That is why your test worked even with a "typo" in the subject!4. Summary of the FlowStepActionLogicOutboundSystem emails broker.Uses webwise5+10245@gcdloads.com as the sender.Broker ReplyBroker hits "Reply."Their email is sent back to that exact unique address.InboundListener receives email.Extracts 10245 from the TO field.MatchDB lookup.Immediately links to Load 10245 without "reading" a single word.
