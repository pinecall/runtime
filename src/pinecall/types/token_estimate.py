"""What a model would count, near enough to cut by: one estimate, and no tokenizer anywhere."""

# Prose runs near 1.3 tokens a word. The chunker cuts a file by this and the contextual embedder
# windows a document by it, so it is one number in one place: two estimates that drift give a
# chunk that fits a cap and a window that does not fit the context it was measured against.
TOKENS_PER_WORD = 1.3


def estimated_tokens(text: str) -> int:
    """What a model would count, near enough to cut by: the words, times 1.3."""
    return round(len(text.split()) * TOKENS_PER_WORD)
