"""What a call's usage cost in US dollars, from the rates of the box's configuration."""

import csv
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pinecall.domain.call import PhoneLeg
from pinecall.domain.errors import DeclarationRefused
from pinecall.providers.catalog import Providers, Rate
from pinecall.wire.metrics import LLMModelUsage, ModelUsage, STTModelUsage, TTSModelUsage
from pinecall.wire.parts import Cost, CostRow, UnpricedRow

type Unit = Literal["input_tokens", "cached_input_tokens", "cache_creation_tokens", "output_tokens"]


A_MILLION = 1_000_000

A_MINUTE_S = 60

# A prices file's unit, and the field of `Rate` it fills.
FIELD_OF_UNIT = {
    "input_tokens": "input",
    "output_tokens": "output",
    "cached_input_tokens": "cached_input",
    "cache_creation_tokens": "cache_creation",
    "characters": "characters",
    "audio_seconds": "audio_seconds",
    "minutes": "minutes",
}

COLUMNS = ("vendor", "model", "unit", "usd", "as_of", "source")


@dataclass(frozen=True)
class RatesChange:
    """What writing a prices file would do to the box's rates, model by model."""

    added: tuple[str, ...]
    changed: tuple[str, ...]
    unchanged: tuple[str, ...]
    # Kept as they are: a file adds and replaces, it never removes.
    only_on_the_box: tuple[str, ...]


def cost(
    usage: Iterable[ModelUsage], configured: Providers, *, legs: Sequence[PhoneLeg] = ()
) -> Cost:
    """Price every usage row and phone leg; what has no rate is listed unpriced, never at zero."""
    rows: list[CostRow] = []
    unpriced: list[UnpricedRow] = []
    for used in usage:
        priced = _priced(used, configured.rates)
        if priced is None:
            unpriced.append(UnpricedRow(provider=used.provider, model=used.model))
        else:
            rows += priced
    for leg in legs:
        billed = _leg_row(leg, configured.rates)
        if billed is None:
            unpriced.append(UnpricedRow(provider=leg.carrier, model=_leg_name(leg)))
        elif billed.quantity > 0:
            rows.append(billed)
    return Cost(usd=round(sum(row.usd for row in rows), 6), rows=rows, unpriced=unpriced)


def rate_of(rates: Mapping[str, Rate], model: str) -> Rate | None:
    """The rate whose key is the longest prefix of the model id, so snapshots price by family."""
    key = _longest_prefix(rates, model)
    return None if key is None else rates[key]


def rates_from_csv(text: str) -> dict[str, Rate]:
    """The rates of a prices file, one row per model and unit; `#` lines are notes."""
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    reader = csv.DictReader(lines)
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise DeclarationRefused(f"a prices file has the columns {','.join(COLUMNS)}")
    fields: dict[str, dict[str, float | str]] = {}
    vendors: dict[str, str] = {}
    for number, row in enumerate(reader, start=2):
        model, unit = row["model"], row["unit"]
        field = FIELD_OF_UNIT.get(unit)
        if field is None:
            raise DeclarationRefused(
                f"line {number}: {unit!r} is not a unit; one of {', '.join(FIELD_OF_UNIT)}"
            )
        if vendors.setdefault(model, row["vendor"]) != row["vendor"]:
            raise DeclarationRefused(
                f"line {number}: {model} is {vendors[model]}'s already: a model is priced once"
            )
        priced = fields.setdefault(model, {"as_of": ""})
        if field in priced:
            raise DeclarationRefused(f"line {number}: {model} {unit} is priced twice")
        priced[field] = _usd(row["usd"], number)
        priced["as_of"] = max(str(priced["as_of"]), row["as_of"])
    return {model: Rate.model_validate(priced) for model, priced in fields.items()}


def rates_changed(box: Mapping[str, Rate], written: Mapping[str, Rate]) -> RatesChange:
    """Each model of the file as new, changed or the same, and what only the box holds."""
    return RatesChange(
        added=tuple(sorted(model for model in written if model not in box)),
        changed=tuple(
            sorted(model for model in written if model in box and box[model] != written[model])
        ),
        unchanged=tuple(sorted(model for model in written if box.get(model) == written[model])),
        only_on_the_box=tuple(sorted(model for model in box if model not in written)),
    )


# Interruption and end-of-turn models run inside livekit and cost nothing: no row, not unpriced.
def _priced(used: ModelUsage, rates: Mapping[str, Rate]) -> list[CostRow] | None:
    rate = rate_of(rates, used.model)
    match used:
        case LLMModelUsage():
            return None if rate is None or rate.input is None else _tokens(used, rate)
        case TTSModelUsage():
            per = None if rate is None else rate.characters
            return _counted(used, "characters", quantity=used.characters_count or 0, per=per)
        case STTModelUsage():
            per = None if rate is None else rate.audio_seconds
            return _counted(used, "audio_seconds", quantity=used.audio_duration or 0.0, per=per)
        case _:
            return []


# Plugins count cached and cache-written tokens inside input_tokens, so both come off the
# fresh input before it is priced.
def _tokens(used: LLMModelUsage, rate: Rate) -> list[CostRow]:
    cached = used.input_cached_tokens or 0
    written = used.input_cache_creation_tokens or 0
    counted: list[tuple[Unit, int, float | None]] = [
        ("input_tokens", max((used.input_tokens or 0) - cached - written, 0), rate.input),
        (
            "cached_input_tokens",
            cached,
            rate.input if rate.cached_input is None else rate.cached_input,
        ),
        ("cache_creation_tokens", written, rate.cache_creation),
        ("output_tokens", used.output_tokens or 0, rate.output),
    ]
    return [
        _row(used, unit, quantity=tokens, per=per, usd=tokens / A_MILLION * per)
        for unit, tokens, per in counted
        if tokens > 0 and per is not None
    ]


def _counted(
    used: TTSModelUsage | STTModelUsage,
    unit: Literal["characters", "audio_seconds"],
    *,
    quantity: float,
    per: float | None,
) -> list[CostRow] | None:
    if per is None:
        return None
    if quantity <= 0:
        return []
    return [_row(used, unit, quantity=quantity, per=per, usd=quantity * per)]


# A token row carries its price per million tokens, as a pricing page prints it.
def _row(
    used: ModelUsage,
    unit: Unit | Literal["characters", "audio_seconds"],
    *,
    quantity: float,
    per: float,
    usd: float,
) -> CostRow:
    return CostRow(
        provider=used.provider,
        model=used.model,
        unit=unit,
        quantity=quantity,
        unit_price_usd=per,
        usd=round(usd, 6),
    )


# A carrier prices a leg by the longest prefix of its number, as its own rate tables do; the row
# names the prefix it matched, never the number.
def _leg_row(leg: PhoneLeg, rates: Mapping[str, Rate]) -> CostRow | None:
    key = _longest_prefix(rates, f"{_leg_name(leg)}/{leg.number}")
    per = None if key is None else rates[key].minutes
    if key is None or per is None:
        return None
    minutes = math.ceil(leg.seconds / A_MINUTE_S) if leg.seconds > 0 else 0
    return CostRow(
        provider=leg.carrier,
        model=key,
        unit="minutes",
        quantity=minutes,
        unit_price_usd=per,
        usd=round(minutes * per, 6),
    )


def _leg_name(leg: PhoneLeg) -> str:
    return f"{leg.carrier}-{leg.direction}"


def _longest_prefix(rates: Mapping[str, Rate], name: str) -> str | None:
    listed = [key for key in rates if name.startswith(key)]
    return max(listed, key=len) if listed else None


def _usd(text: str, number: int) -> float:
    try:
        usd = float(text)
    except ValueError:
        raise DeclarationRefused(f"line {number}: {text!r} is not a price in dollars") from None
    if usd < 0:
        raise DeclarationRefused(f"line {number}: a price is never below zero")
    return usd
