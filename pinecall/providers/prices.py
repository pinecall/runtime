"""What a call's usage cost in US dollars, from the rates of the box's configuration."""

from collections.abc import Iterable, Mapping
from typing import Literal

from pinecall.providers.catalog import Providers, Rate
from pinecall.wire.metrics import LLMModelUsage, ModelUsage, STTModelUsage, TTSModelUsage
from pinecall.wire.parts import Cost, CostRow, UnpricedRow

type Unit = Literal["input_tokens", "cached_input_tokens", "cache_creation_tokens", "output_tokens"]


A_MILLION = 1_000_000


def cost(usage: Iterable[ModelUsage], configured: Providers) -> Cost:
    """Price every usage row; a model with no rate is listed unpriced, never priced at zero."""
    rows: list[CostRow] = []
    unpriced: list[UnpricedRow] = []
    for used in usage:
        priced = _priced(used, configured.rates)
        if priced is None:
            unpriced.append(UnpricedRow(provider=used.provider, model=used.model))
        else:
            rows += priced
    return Cost(usd=round(sum(row.usd for row in rows), 6), rows=rows, unpriced=unpriced)


def rate_of(rates: Mapping[str, Rate], model: str) -> Rate | None:
    """The rate whose key is the longest prefix of the model id, so snapshots price by family."""
    listed = [name for name in rates if model.startswith(name)]
    return rates[max(listed, key=len)] if listed else None


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
