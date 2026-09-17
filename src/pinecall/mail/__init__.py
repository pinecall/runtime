"""Outbound mail: the letters this gateway writes, and the generic SMTP server it hands them to."""

from pinecall.mail.letters import (
    Letter,
    a_forgotten_password,
    a_reset,
    an_invitation,
    where_the_card_is,
)
from pinecall.mail.outbox import Outbox, outbox_for, the_boxs_mailbox
from pinecall.mail.smtp import MailRefused, posted

__all__ = [
    "Letter",
    "MailRefused",
    "Outbox",
    "a_forgotten_password",
    "a_reset",
    "an_invitation",
    "outbox_for",
    "posted",
    "the_boxs_mailbox",
    "where_the_card_is",
]
