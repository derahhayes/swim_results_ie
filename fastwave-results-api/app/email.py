"""Email sending - no real provider integration yet.

send_email() renders a template + context into {subject, body} and logs
it instead of actually sending. Verification links, claim/affiliation
decision notices, and password resets all go through this - swapping in
a real provider later only touches this one module.

# TODO: wire to real provider (e.g. Postmark/SES) before public launch.
"""

import logging

logger = logging.getLogger("app.email")
logger.setLevel(logging.INFO)
if not logger.handlers:
    # Nothing in this app calls logging.basicConfig() / configures the root
    # logger, so with no handler here, logger.info() below would be
    # silently dropped everywhere (Python's logging default is WARNING,
    # and the root logger's "handler of last resort" only emits WARNING+)
    # - there'd be no EMAIL log line to find in Railway's logs at all,
    # regardless of which log view you check. propagate=False keeps this
    # self-contained rather than depending on root logger config existing
    # (or not existing twice) elsewhere.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    logger.addHandler(_handler)
    logger.propagate = False

TEMPLATES: dict[str, dict[str, str]] = {
    "verify_email": {
        "subject": "Verify your Fastwave Results email",
        "body": "Hi {displayName},\n\nVerify your email:\n{verify_url}\n\nThis link expires in 24 hours.",
    },
    "password_reset": {
        "subject": "Reset your Fastwave Results password",
        "body": (
            "Hi {displayName},\n\nReset your password:\n{reset_url}\n\n"
            "This link expires in 1 hour. If you didn't request this, ignore this email."
        ),
    },
    "claim_approved": {
        "subject": "Your swimmer claim was approved",
        "body": "Hi {displayName},\n\nYour claim for {swimmerName} has been approved.",
    },
    "claim_rejected": {
        "subject": "Your swimmer claim was not approved",
        "body": "Hi {displayName},\n\nYour claim for {swimmerName} was not approved.\nReason: {reason}",
    },
    "affiliation_approved": {
        "subject": "Your coach affiliation was approved",
        "body": "Hi {displayName},\n\nYour coach affiliation with {clubName} has been approved.",
    },
    "affiliation_rejected": {
        "subject": "Your coach affiliation was not approved",
        "body": "Hi {displayName},\n\nYour coach affiliation with {clubName} was not approved.\nReason: {reason}",
    },
}


def send_email(to: str, template: str, **ctx: object) -> None:
    spec = TEMPLATES[template]
    subject = spec["subject"].format(**ctx)
    body = spec["body"].format(**ctx)
    logger.info("EMAIL to=%s subject=%r\n%s", to, subject, body)
