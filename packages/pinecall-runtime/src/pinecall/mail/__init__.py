"""Outbound mail: the letters this gateway writes, and the generic SMTP server it hands them to."""

from pinecall.mail.box_mailbox import BoxMail, TheBoxsMail, environment_mailbox
from pinecall.mail.brand import Brand, apply_brand, brand_of
from pinecall.mail.letters import (
    Letter,
    card_link,
    forgotten_password_letter,
    invitation_letter,
    probe_letter,
    reset_letter,
    signup_code_letter,
)
from pinecall.mail.outbox import Outbox, outbox_for
from pinecall.mail.smtp import MailRefused, post

__all__ = [
    "BoxMail",
    "Brand",
    "Letter",
    "MailRefused",
    "Outbox",
    "TheBoxsMail",
    "apply_brand",
    "brand_of",
    "card_link",
    "environment_mailbox",
    "forgotten_password_letter",
    "invitation_letter",
    "outbox_for",
    "post",
    "probe_letter",
    "reset_letter",
    "signup_code_letter",
]
