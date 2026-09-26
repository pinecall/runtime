"""The fixed secrets a suite seals and signs with, written down so an assertion can name them."""

# The box's vault key. Fernet's own generator, run once and written down: a suite that generated
# one per run would encrypt with a key no assertion could name.
A_VAULT_KEY = "Zm9yLXJpbmctMC1vbmx5LW5vYm9keS13aWxsLXVzZT0="
