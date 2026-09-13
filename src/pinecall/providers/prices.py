"""What a model costs, in euros, with the rate and the date written down beside every number."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast

from pinecall.providers.published import published
from pinecall_protocol.defs import Cost, CostRate, CostRow, UnpricedRow
from pinecall_protocol.metrics import LLMModelUsage, ModelUsage, STTModelUsage, TTSModelUsage

# The table is USD per million tokens, read off each vendor's public pricing page on this date, and
# the euro is one conversion at one stated rate. Two numbers with a date beat one number nobody can
# reproduce: when a price moves, the diff of this file says what changed and when it did.
AS_OF = "2026-09-06"
USD_TO_EUR = 0.92

RATE = CostRate(currency="EUR", usd_to_eur=USD_TO_EUR, as_of=AS_OF)

A_MILLION = 1_000_000

# The units a token-billed model is charged in, as CostRow names them.
type TokenUnit = Literal[
    "input_tokens", "cached_input_tokens", "cache_creation_tokens", "output_tokens"
]

# The units a voice is billed in: a TTS row counts characters, an STT row counts seconds of audio.
type MediaUnit = Literal["characters", "audio_seconds"]


# Cache reads and cache writes are priced apart because they are priced apart: Anthropic bills a
# read at a tenth of the input price and a write at a quarter above it, and a summary that folded
# them into one input number would misprice every cached call in both directions.
@dataclass(frozen=True)
class Price:
    """USD per million tokens for one model, by what the tokens were."""

    input: float
    output: float
    cached_input: float
    cache_creation: float | None = None


# By the longest prefix that matches, because a model id carries a date: claude-haiku-4-5-20251001
# is priced by claude-haiku-4-5, and a snapshot nobody listed is unpriced rather than guessed.
PRICES: dict[str, Price] = {
    # Anthropic, read off claude.com/pricing on AS_OF. Fable 5.1 is the exception in the family:
    # its cache read is a fortieth of input, not a tenth, and it is priced that way on purpose.
    "claude-fable-5-1": Price(input=10.00, output=50.00, cached_input=0.25, cache_creation=12.50),
    "claude-opus-5": Price(input=5.00, output=25.00, cached_input=0.50, cache_creation=6.25),
    "claude-sonnet-5": Price(input=2.00, output=10.00, cached_input=0.20, cache_creation=2.50),
    "claude-haiku-4-5": Price(input=1.00, output=5.00, cached_input=0.10, cache_creation=1.25),
    "claude-sonnet-4-5": Price(input=3.00, output=15.00, cached_input=0.30, cache_creation=3.75),
    "claude-opus-4-5": Price(input=5.00, output=25.00, cached_input=0.50, cache_creation=6.25),
    # OpenAI's pricing page refused the fetch on AS_OF (403). These four rows are the last ones
    # anybody read there and nobody has re-read since: unverified 2026-09-06.
    "gpt-4.1-mini": Price(input=0.40, output=1.60, cached_input=0.10),
    "gpt-4.1": Price(input=2.00, output=8.00, cached_input=0.50),
    "gpt-4o-mini": Price(input=0.15, output=0.60, cached_input=0.075),
    "gpt-4o": Price(input=2.50, output=10.00, cached_input=1.25),
}


# A voice vendor lists its price per thousand characters, per minute or per hour; the usage row
# counts one character or one second, so the price is written the way the page says it and divided
# here, once, where a reader can check the arithmetic against the page.
@dataclass(frozen=True)
class MediaPrice:
    """USD per one unit — one character, one second — for a voice model."""

    unit: MediaUnit
    usd_per_unit: float


A_THOUSAND = 1_000
A_MINUTE_S = 60
AN_HOUR_S = 3_600

# Keyed by model prefix like PRICES, and never by the provider label: the plugins spell theirs
# "ElevenLabs" and "Soniox" and the model ids do not collide. Read off each vendor's pricing page
# on MEDIA_AS_OF. Deepgram showed a limited-time promotional rate that day; the regular one is the
# one written, because a promotion ends and a table that says the sale price is wrong the day after.
MEDIA_AS_OF = "2026-09-07"
MEDIA_PRICES: dict[str, MediaPrice] = {
    # ElevenLabs, elevenlabs.io/pricing/api: $0.05 per 1k characters Flash, $0.10 v2 and v3.
    "eleven_flash": MediaPrice("characters", 0.05 / A_THOUSAND),
    "eleven_multilingual": MediaPrice("characters", 0.10 / A_THOUSAND),
    "eleven_v3": MediaPrice("characters", 0.10 / A_THOUSAND),
    # Soniox, soniox.com/pricing: $0.12 per hour of real-time audio, every stt-rt model.
    "stt-rt": MediaPrice("audio_seconds", 0.12 / AN_HOUR_S),
    # Deepgram, deepgram.com/pricing: $0.0077 per streamed minute for Flux and for Nova-3
    # monolingual (multilingual Nova-3 is $0.0092; the row cannot tell the two apart and the
    # cheaper is written). The page lists one Flux line, so flux-general-multi is priced by it.
    "flux-general": MediaPrice("audio_seconds", 0.0077 / A_MINUTE_S),
    "nova-3": MediaPrice("audio_seconds", 0.0077 / A_MINUTE_S),
}


# Two tables, and the order between them is the whole of the rule: OURS first. The rows above were
# read off each vendor's own page on a stated date, in the units a livekit usage row carries, and
# where they disagree with the published list they disagree knowingly — Soniox bills a realtime
# hour and the published list prices it by token, which an audio-seconds row cannot feed at all.
# Behind them, providers/published.py prices the other forty vendors, which is the difference
# between a Cartesia call reading `unpriced` and reading its bill. See that module's header.
def price_of(model: str) -> Price | None:
    """The token price for a model id, by the longest listed prefix. None: unpriced."""
    ours = _by_longest_prefix(PRICES, model)
    return ours if ours is not None else _a_published_price(model)


def media_price_of(model: str) -> MediaPrice | None:
    """The per-character or per-second price for a voice model id. None: unpriced."""
    ours = _by_longest_prefix(MEDIA_PRICES, model)
    return ours if ours is not None else _a_published_media_price(model)


# A published row is kept only when it carries both halves of a token bill; a cache rate it does
# not name is None, which _rows_of already reads as "this model is not billed for that".
def _a_published_price(model: str) -> Price | None:
    """One published token row as a Price, or None when nobody published that model."""
    row = _by_longest_prefix(published().tokens, model)
    if row is None:
        return None
    return Price(
        input=row["input"],
        output=row["output"],
        cached_input=row.get("cached_input", row["input"]),
        cache_creation=row.get("cache_creation"),
    )


def _a_published_media_price(model: str) -> MediaPrice | None:
    """One published voice row as a MediaPrice. The unit is theirs; the arithmetic is ours."""
    row = _by_longest_prefix(published().media, model)
    if row is None:
        return None
    unit, usd = row
    return MediaPrice(cast("MediaUnit", unit), usd)


# Interruption and end-of-turn rows are livekit's own models, run on this box (inference/): there is
# no bill, so they are neither a line nor an unpriced row. Every other row is one or the other.
def cost_of(usage: Sequence[ModelUsage]) -> Cost:
    """Price every usage row. A model the tables do not know is listed unpriced, never at zero."""
    rows: list[CostRow] = []
    unpriced: list[UnpricedRow] = []
    for used in usage:
        priced = _priced(used)
        if priced is None:
            unpriced.append(UnpricedRow(provider=used.provider, model=used.model))
            continue
        rows.extend(priced)
    return Cost(eur=round(sum(row.eur for row in rows), 6), rate=RATE, rows=rows, unpriced=unpriced)


# What ring 4 writes on `call.score`, and the one thing a count of judge calls can never be. A
# judgment carries no cost of its own (livekit's JudgmentResult is a verdict, a reasoning and the
# instructions), so the judge's tokens are priced here like every other LLM row in this runtime.
# None and never 0.0: a model nobody reported usage for has an unknown bill, not a free one.
def eur_of(usage: Sequence[ModelUsage]) -> float | None:
    """What these usage rows came to in euros. None when not one of them could be priced."""
    cost = cost_of(usage)
    return cost.eur if cost.rows else None


def _priced(used: ModelUsage) -> list[CostRow] | None:
    """The lines one row comes to; None when its model is unknown; [] when nothing is owed."""
    match used:
        case LLMModelUsage():
            price = price_of(used.model)
            return None if price is None else _rows_of(used, price)
        case TTSModelUsage():
            return _media_rows(used, used.characters_count or 0)
        case STTModelUsage():
            return _media_rows(used, used.audio_duration or 0.0)
        case _:
            return []


def _by_longest_prefix[T](table: dict[str, T], model: str) -> T | None:
    """The entry whose key is the longest prefix of the model id: a snapshot is priced by family."""
    listed = [name for name in table if model.startswith(name)]
    return table[max(listed, key=len)] if listed else None


# input_tokens includes the cached ones, the way every provider reports it, so the uncached part is
# the subtraction. Charging the whole of it at the input price would bill a cache hit twice.
def _rows_of(used: LLMModelUsage, price: Price) -> list[CostRow]:
    """One priced line per unit this model was billed in; a unit with no tokens is not a line."""
    cached = used.input_cached_tokens or 0
    written = used.input_cache_creation_tokens or 0
    fresh = max((used.input_tokens or 0) - cached, 0)
    counted: list[tuple[TokenUnit, int, float | None]] = [
        ("input_tokens", fresh, price.input),
        ("cached_input_tokens", cached, price.cached_input),
        ("cache_creation_tokens", written, price.cache_creation),
        ("output_tokens", used.output_tokens or 0, price.output),
    ]
    return [
        _a_row(used, unit, tokens, usd)
        for unit, tokens, usd in counted
        if tokens > 0 and usd is not None
    ]


# A token line carries the list price per million, the number the pricing page prints and the one a
# reader will check it against; a voice line carries the price per one character or one second, the
# unit its quantity is counted in, because the pages disagree on theirs (a thousand, a minute, an
# hour). Either way eur is what the quantity came to at that price.
def _a_row(used: LLMModelUsage, unit: TokenUnit, tokens: int, usd_per_mtok: float) -> CostRow:
    """One line of the bill: what was counted, how much of it, and what it came to in euros."""
    eur = tokens / A_MILLION * usd_per_mtok * USD_TO_EUR
    return CostRow(
        provider=used.provider,
        model=used.model,
        unit=unit,
        quantity=tokens,
        unit_price_usd=usd_per_mtok,
        eur=round(eur, 6),
    )


def _media_rows(used: TTSModelUsage | STTModelUsage, quantity: float) -> list[CostRow] | None:
    """The one line a voice row comes to; none when nothing was counted; None when unpriced."""
    price = media_price_of(used.model)
    if price is None:
        return None
    if quantity <= 0:
        return []
    return [
        CostRow(
            provider=used.provider,
            model=used.model,
            unit=price.unit,
            quantity=quantity,
            unit_price_usd=price.usd_per_unit,
            eur=round(quantity * price.usd_per_unit * USD_TO_EUR, 6),
        )
    ]
