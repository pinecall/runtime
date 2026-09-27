"""Every livekit-agents 1.8 metric and usage row, verbatim."""

from typing import Annotated, Literal

from pydantic import Field

from pinecall.wire.frames import WireModel


class Metadata(WireModel):
    """Which model and provider produced a block."""

    model_name: str | None = None
    model_provider: str | None = None


class LLMMetrics(WireModel):
    """One LLM request, measured by the session's llm node."""

    type: Literal["llm_metrics"] = "llm_metrics"
    label: str
    request_id: str
    timestamp: float
    duration: float
    ttft: float
    cancelled: bool
    completion_tokens: int
    prompt_tokens: int
    prompt_cached_tokens: int
    cache_creation_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int
    tokens_per_second: float
    speech_id: str | None = None
    metadata: Metadata | None = None


class STTMetrics(WireModel):
    """One speech-to-text request, measured by the session's stt node."""

    type: Literal["stt_metrics"] = "stt_metrics"
    label: str
    request_id: str
    timestamp: float
    duration: float
    audio_duration: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    streamed: bool
    acquire_time: float | None = None
    connection_reused: bool | None = None
    metadata: Metadata | None = None


class TTSMetrics(WireModel):
    """One text-to-speech request, measured by the session's tts node."""

    type: Literal["tts_metrics"] = "tts_metrics"
    label: str
    request_id: str
    timestamp: float
    ttfb: float
    duration: float
    audio_duration: float
    cancelled: bool
    characters_count: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    streamed: bool
    acquire_time: float | None = None
    connection_reused: bool | None = None
    segment_id: str | None = None
    speech_id: str | None = None
    metadata: Metadata | None = None


class VADMetrics(WireModel):
    """The voice activity detector's health, reported about once a second while it runs."""

    type: Literal["vad_metrics"] = "vad_metrics"
    label: str
    timestamp: float
    idle_time: float
    inference_duration_total: float
    inference_count: int
    metadata: Metadata | None = None


class EOUMetrics(WireModel):
    """How long the session took to decide that the caller had finished."""

    type: Literal["eou_metrics"] = "eou_metrics"
    timestamp: float
    end_of_utterance_delay: float
    transcription_delay: float
    on_user_turn_completed_delay: float
    speech_id: str | None = None
    metadata: Metadata | None = None


class EOTInferenceMetrics(WireModel):
    """One prediction by the end-of-turn model."""

    type: Literal["eot_inference_metrics"] = "eot_inference_metrics"
    timestamp: float
    total_duration: float
    detection_delay: float
    prediction_duration: float
    num_requests: int | None = None
    metadata: Metadata | None = None


class InterruptionMetrics(WireModel):
    """The interruption detector's latest inference and its running counts."""

    type: Literal["interruption_metrics"] = "interruption_metrics"
    timestamp: float
    total_duration: float
    prediction_duration: float
    detection_delay: float
    num_interruptions: int
    num_backchannels: int
    num_requests: int
    metadata: Metadata | None = None


class RealtimeCachedTokenDetails(WireModel):
    """Of a realtime model's cached input, how much was audio, text or image."""

    audio_tokens: int | None = None
    text_tokens: int | None = None
    image_tokens: int | None = None


class RealtimeInputTokenDetails(WireModel):
    """What a realtime model read, by kind, with the cached part broken out."""

    audio_tokens: int | None = None
    text_tokens: int | None = None
    image_tokens: int | None = None
    cached_tokens: int | None = None
    cached_tokens_details: RealtimeCachedTokenDetails | None = None


class RealtimeOutputTokenDetails(WireModel):
    """What a realtime model produced, by kind."""

    text_tokens: int | None = None
    audio_tokens: int | None = None
    image_tokens: int | None = None


class RealtimeModelMetrics(WireModel):
    """One response from a speech-to-speech model, which replaces STT, LLM and TTS at once."""

    type: Literal["realtime_model_metrics"] = "realtime_model_metrics"
    label: str | None = None
    request_id: str
    timestamp: float
    duration: float | None = None
    session_duration: float | None = None
    ttft: float | None = None
    cancelled: bool | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    tokens_per_second: float | None = None
    input_token_details: RealtimeInputTokenDetails
    output_token_details: RealtimeOutputTokenDetails
    acquire_time: float | None = None
    connection_reused: bool | None = None
    metadata: Metadata | None = None


class AvatarMetrics(WireModel):
    """Timing of a video avatar worker, when one is in the chain."""

    type: Literal["avatar_metrics"] = "avatar_metrics"
    timestamp: float
    playback_latency: float | None = None
    session_started_time: float | None = None
    avatar_joined_time: float | None = None
    metadata: Metadata | None = None


class TurnMetadata(WireModel):
    """Which model handled one leg of a turn."""

    model_name: str | None = None
    model_provider: str | None = None


# Every field is optional: a text session has no speech to time.
class UserTurnMetrics(WireModel):
    """What the session measured about the caller's turn."""

    started_speaking_at: float | None = None
    stopped_speaking_at: float | None = None
    transcription_delay: float | None = None
    end_of_turn_delay: float | None = None
    on_user_turn_completed_delay: float | None = None
    stt_metadata: TurnMetadata | None = None


class AgentTurnMetrics(WireModel):
    """What the session measured about the agent's reply."""

    started_speaking_at: float | None = None
    stopped_speaking_at: float | None = None
    llm_node_ttft: float | None = None
    llm_node_tps: float | None = None
    llm_node_ttfs: float | None = None
    tts_node_ttfb: float | None = None
    playback_latency: float | None = None
    e2e_latency: float | None = None
    provider_request_ids: list[str] | None = None
    llm_metadata: TurnMetadata | None = None
    tts_metadata: TurnMetadata | None = None


class LLMModelUsage(WireModel):
    """Everything one LLM consumed over the call."""

    type: Literal["llm_usage"] = "llm_usage"
    provider: str
    model: str
    input_tokens: int | None = None
    input_cached_tokens: int | None = None
    input_cache_creation_tokens: int | None = None
    input_audio_tokens: int | None = None
    input_cached_audio_tokens: int | None = None
    input_text_tokens: int | None = None
    input_cached_text_tokens: int | None = None
    input_image_tokens: int | None = None
    input_cached_image_tokens: int | None = None
    output_tokens: int | None = None
    output_audio_tokens: int | None = None
    output_text_tokens: int | None = None
    output_reasoning_tokens: int | None = None
    session_duration: float | None = None


class TTSModelUsage(WireModel):
    """Everything one TTS consumed over the call."""

    type: Literal["tts_usage"] = "tts_usage"
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    characters_count: int | None = None
    audio_duration: float | None = None


class STTModelUsage(WireModel):
    """Everything one STT consumed over the call."""

    type: Literal["stt_usage"] = "stt_usage"
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    audio_duration: float | None = None


class InterruptionModelUsage(WireModel):
    """How often the interruption detector was asked over the call."""

    type: Literal["interruption_usage"] = "interruption_usage"
    provider: str
    model: str
    total_requests: int | None = None


class EOTModelUsage(WireModel):
    """How often the end-of-turn model was asked over the call."""

    type: Literal["eot_usage"] = "eot_usage"
    provider: str
    model: str
    total_requests: int | None = None


type ModelUsage = Annotated[
    LLMModelUsage | TTSModelUsage | STTModelUsage | InterruptionModelUsage | EOTModelUsage,
    Field(discriminator="type"),
]
