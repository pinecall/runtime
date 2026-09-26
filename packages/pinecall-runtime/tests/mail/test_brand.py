"""The brand: what may be set, what a field left out or emptied does, and what a row reads as."""

import pytest
from cryptography.fernet import Fernet

from pinecall.mail import Brand, apply_brand, brand_of
from pinecall.mail.brand import ACCENT, NAME
from pinecall.orgs.box_settings import BRAND
from pinecall.orgs.box_settings_memory import MemoryBoxSettings
from pinecall.types import DeclarationRefused

pytestmark = pytest.mark.unit


def test_a_box_told_nothing_is_pinecall_with_its_accent_and_no_logo() -> None:
    assert Brand().as_json == {"name": NAME, "logo_url": None, "accent": ACCENT}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("name", "x" * 61),
        ("name", "Acme\nVoice"),
        ("accent", "red"),
        ("accent", "#fff"),
        ("accent", "#5b3df5;color:red"),
        ("logo_url", "http://cdn.example.com/mark.png"),
        ("logo_url", 'https://cdn.example.com/x" onerror="alert(1)'),
        ("logo_url", "https:///nowhere"),
    ],
)
def test_what_is_refused_before_it_is_written_into_every_letter(field: str, value: str) -> None:
    with pytest.raises(DeclarationRefused):
        Brand(**{field: value})


def test_a_field_left_out_keeps_what_it_had_and_an_empty_one_goes_back_to_the_default() -> None:
    theirs = Brand(name="Acme Voice", logo_url="https://cdn.example.com/mark.png", accent="#ff6600")
    assert apply_brand(theirs, accent="#00AA00") == Brand(
        name="Acme Voice", logo_url="https://cdn.example.com/mark.png", accent="#00aa00"
    )
    assert apply_brand(theirs, logo_url="") == Brand(name="Acme Voice", accent="#ff6600")
    assert apply_brand(theirs, name="", accent="") == Brand(
        logo_url="https://cdn.example.com/mark.png"
    )
    with pytest.raises(DeclarationRefused):
        apply_brand(theirs, accent="blue")


async def test_the_brand_is_read_off_the_box_row_and_a_row_nobody_can_use_reads_as_default() -> (
    None
):
    box = MemoryBoxSettings(Fernet(Fernet.generate_key()))
    assert await brand_of(None) == Brand() and await brand_of(box) == Brand()
    await box.put(BRAND, {"name": "Acme Voice", "logo_url": None, "accent": "#ff6600"})
    assert await brand_of(box) == Brand(name="Acme Voice", accent="#ff6600")
    await box.put(BRAND, {"name": "Acme Voice", "accent": "not a colour"})
    assert await brand_of(box) == Brand(), "a letter still leaves"
