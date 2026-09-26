"""A process that read a key for every vendor: what a suite building a whole pipeline runs on."""

from pinecall.settings import Settings

A_KEY = "nobody-will-ever-deploy-this"


def keyed_for_every_vendor() -> Settings:
    """A process that read a key for every vendor this suite builds."""
    return Settings(
        world="production",
        anthropic_api_key=A_KEY,
        openai_api_key=A_KEY,
        soniox_api_key=A_KEY,
        deepgram_api_key=A_KEY,
        eleven_api_key=A_KEY,
        cartesia_api_key=A_KEY,
    )
