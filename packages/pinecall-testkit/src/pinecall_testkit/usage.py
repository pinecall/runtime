"""The call.summary and call.score a usage test writes: one call's cost, as the log keeps it."""

from pinecall.types.json import JsonObject

A_SUMMARY: JsonObject = {
    "reason": "hangup",
    "outcome": "booked",
    "duration_s": 90.0,
    "turns": 6,
    "usage": [
        {
            "type": "llm",
            "provider": "anthropic",
            "model": "h",
            "input_tokens": 1200,
            "output_tokens": 300,
        },
        {"type": "tts", "provider": "elevenlabs", "model": "v3", "characters_count": 450},
        {"type": "stt", "provider": "soniox", "model": "x", "audio_duration": 88.0},
    ],
    "cost": {"eur": 0.012, "rate": {}, "rows": [], "unpriced": []},
}
A_SCORE: JsonObject = {"passed": True, "judges": [], "judge_calls": 2, "judge_cost_eur": 0.001}

# What a call nobody judged writes: `CallScore.judge_cost_eur` is `float | None`, so it serialises
# as null and not as absent — which is a different thing from a key that is missing.
A_SCORE_NOBODY_JUDGED: JsonObject = {
    "passed": None,
    "not_judged": "no judge was asked",
    "judges": [],
    "panel": None,
    "judge_calls": 0,
    "judge_cost_eur": None,
}
