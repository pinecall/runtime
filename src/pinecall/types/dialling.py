"""What an org may dial out: a destination, the country it reaches, and the policy it runs under."""

from dataclasses import dataclass
from typing import Literal, cast, get_args

from pinecall.types.refused import DeclarationRefused
from pinecall.types.route import an_e164

# How livekit-sip carries the INVITE we place. `auto` lets the SFU pick, which is what a peer that
# said nothing gets; Twilio's termination takes any of the three and we say nothing either.
type SipTransport = Literal["auto", "udp", "tcp", "tls"]
SIP_TRANSPORTS: frozenset[str] = frozenset(get_args(SipTransport.__value__))

# Every country calling code E.164 assigns, longest match first — the one table that turns a
# number into the country it reaches. It is here and not in a vendor's library because the only
# question asked of it is "is this destination in the same country as one of the org's own
# numbers", and that question must answer the same way on a box with no network.
#
# The one-digit codes are the exception that makes longest-match safe: no assigned code begins
# with 1 or 7 and is longer than one digit, so +1242 is the NANP and never a country of its own.
CALLING_CODES: frozenset[str] = frozenset(
    {
        "1", "7",
        "20", "27", "30", "31", "32", "33", "34", "36", "39",
        "40", "41", "43", "44", "45", "46", "47", "48", "49",
        "51", "52", "53", "54", "55", "56", "57", "58",
        "60", "61", "62", "63", "64", "65", "66",
        "81", "82", "84", "86",
        "90", "91", "92", "93", "94", "95", "98",
        "211", "212", "213", "216", "218",
        "220", "221", "222", "223", "224", "225", "226", "227", "228", "229",
        "230", "231", "232", "233", "234", "235", "236", "237", "238", "239",
        "240", "241", "242", "243", "244", "245", "246", "247", "248", "249",
        "250", "251", "252", "253", "254", "255", "256", "257", "258",
        "260", "261", "262", "263", "264", "265", "266", "267", "268", "269",
        "290", "291", "297", "298", "299",
        "350", "351", "352", "353", "354", "355", "356", "357", "358", "359",
        "370", "371", "372", "373", "374", "375", "376", "377", "378", "379",
        "380", "381", "382", "383", "385", "386", "387", "389",
        "420", "421", "423",
        "500", "501", "502", "503", "504", "505", "506", "507", "508", "509",
        "590", "591", "592", "593", "594", "595", "596", "597", "598", "599",
        "670", "672", "673", "674", "675", "676", "677", "678", "679",
        "680", "681", "682", "683", "685", "686", "687", "688", "689",
        "690", "691", "692",
        "800", "808", "850", "852", "853", "855", "856",
        "870", "878", "880", "881", "882", "883", "886", "888",
        "960", "961", "962", "963", "964", "965", "966", "967", "968",
        "970", "971", "972", "973", "974", "975", "976", "977", "979",
        "992", "993", "994", "995", "996", "998",
    }
)  # fmt: skip

# The codes that are a bill and never a person this org has spoken to: the satellite networks and
# the two global service ranges. International revenue-share fraud is dialled into exactly these —
# a compromised trunk placing calls to +882 at four euros a minute, overnight, is the classic
# shape — and no call BACK is ever to one of them, which is what makes refusing them free.
# A national premium range (+34 80x, +1 900) is not here: the country allowlist and the rule that
# a destination must already have called are what stop those, and a list of them would rot.
NEVER_DIALLED: tuple[str, ...] = ("870", "878", "881", "882", "883", "888", "979")

# Under this, it is a short code, a service number or a typo: never a number that called us.
SHORTEST_NATIONAL = 5

NOT_A_COUNTRY = "{number} starts with no country calling code E.164 assigns"
TOO_SHORT = "{number} is shorter than a number anybody calls from: {digits} digits after +{code}"
A_BILL = "{number} is +{code}, a satellite or global-service range this box never dials"

# Defaults chosen for a box that has just turned dialling on, not for a call centre: six a minute
# is a person clicking call back, two hundred a day is a busy desk, and ten minutes is longer than
# any call back needs to be. An operator raises each one deliberately, per org.
DIALS_A_MINUTE = 6
DIALS_A_DAY = 200
LONGEST_CALL_S = 600


@dataclass(frozen=True)
class Destination:
    """A number this box was asked to dial, already judged to be one somebody could answer."""

    number: str
    code: str

    @property
    def national(self) -> str:
        """The digits after the country calling code: how long a number this country has."""
        return self.number[1 + len(self.code) :]


def a_destination(number: str) -> Destination:
    """The number to dial, or a refusal saying which shape or which range put it out of reach."""
    said = an_e164(number)
    code = calling_code(said)
    if code is None:
        raise DeclarationRefused(NOT_A_COUNTRY.format(number=said))
    if code in NEVER_DIALLED:
        raise DeclarationRefused(A_BILL.format(number=said, code=code))
    destination = Destination(number=said, code=code)
    if len(destination.national) < SHORTEST_NATIONAL:
        raise DeclarationRefused(
            TOO_SHORT.format(number=said, digits=len(destination.national), code=code)
        )
    return destination


def calling_code(number: str) -> str | None:
    """The country calling code this E.164 number reaches, longest match, or None for no country."""
    digits = number.removeprefix("+")
    for length in (3, 2, 1):
        if digits[:length] in CALLING_CODES:
            return digits[:length]
    return None


# None on a field is what the org was never told, and the column is NULL: the defaults above are
# what a box means by "nobody has set this", so a row that exists only to hold `dial_anywhere`
# still dials at six a minute. Zero is a real limit and refuses everything, as a quota's is.
@dataclass(frozen=True)
class DialPolicy:
    """What one org may dial: where, how often, for how long, and whether only its own callers."""

    # The one guard an operator lifts by hand: off, a destination must already have called or
    # written to one of this org's agents — "call back" means back. On, the org dials strangers,
    # which is a telemarketer and a decision somebody makes with their name on it.
    dial_anywhere: bool = False
    per_minute: int = DIALS_A_MINUTE
    per_day: int = DIALS_A_DAY
    # The calling codes this org may reach. Empty is not "anywhere": it means the codes of the
    # org's OWN numbers, worked out per dial, which is the fence a tenant never has to configure.
    countries: tuple[str, ...] = ()
    max_duration_s: int = LONGEST_CALL_S

    def __post_init__(self) -> None:
        for name in ("per_minute", "per_day", "max_duration_s"):
            limit: int = getattr(self, name)
            if limit < 0:
                raise DeclarationRefused(f"{name} is a count, and cannot be {limit}")
        for code in self.countries:
            if code not in CALLING_CODES:
                raise DeclarationRefused(f"{code!r} is no country calling code E.164 assigns")

    def reaches(self, destination: Destination, own: tuple[str, ...]) -> bool:
        """Whether the org may reach that country; with none named, its own numbers' are."""
        allowed = self.countries or own
        return destination.code in allowed


def a_sip_transport(word: str | None) -> SipTransport:
    """The transport this word names, or a refusal that lists the four. Unsaid is `auto`."""
    if word is None or word == "":
        return "auto"
    if word not in SIP_TRANSPORTS:
        raise DeclarationRefused(
            f"a SIP peer is dialled over one of {sorted(SIP_TRANSPORTS)}, not {word!r}"
        )
    return cast("SipTransport", word)
