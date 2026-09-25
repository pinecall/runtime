"""Outbound mail: the letters this gateway writes, and the generic SMTP server it hands them to."""

from pinecall.mail.box import BoxMail, TheBoxsMail, the_environments_mailbox
from pinecall.mail.brand import Brand, rebranded, the_brand
from pinecall.mail.letters import (
    Letter,
    a_forgotten_password,
    a_reset,
    a_signup_code,
    a_test_message,
    an_invitation,
    where_the_card_is,
)
from pinecall.mail.outbox import Outbox, outbox_for
from pinecall.mail.smtp import MailRefused, posted

__all__ = [
    "BoxMail",
    "Brand",
    "Letter",
    "MailRefused",
    "Outbox",
    "TheBoxsMail",
    "a_forgotten_password",
    "a_reset",
    "a_signup_code",
    "a_test_message",
    "an_invitation",
    "outbox_for",
    "posted",
    "rebranded",
    "the_brand",
    "the_environments_mailbox",
    "where_the_card_is",
]
