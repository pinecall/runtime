"""One UDP SIP dialogue against a box: the INVITE it sends, and the ACK and BYE that close it."""

import random
import re
import socket
import time

SIP_PORT = 5060

# A caller waits about this long for a box to say anything at all; past it the INVITE was dropped.
ANSWER_TIMEOUT_S = 12.0

# The audio the probe offers and never sends: the codec every carrier speaks, plus DTMF.
OFFERED_AUDIO_PORT = 40000


class SipCall:
    """The one dialogue a probe needs, over one UDP socket it opens and closes itself."""

    def __init__(self, host: str, domain: str, dialled: str, caller: str) -> None:
        self._host = host
        self._domain = domain
        self._dialled = dialled
        self._caller = caller
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.settimeout(3.0)
        self._socket.bind(("0.0.0.0", 0))
        self._local_address = _the_address_that_reaches(host)
        self._local_port = int(self._socket.getsockname()[1])
        self._call_id, self._tag = _token(16), _token(8)
        self._remote_tag = ""
        self._target = f"sip:{dialled}@{domain}"

    @property
    def where_it_calls_from(self) -> str:
        """The address the box will see, which is the address the allow-list has to carry."""
        return f"{self._local_address}:{self._local_port}"

    # One INVITE, and never a second: a retry loop against a carrier-facing port is what a
    # flood looks like from the box's side, and one dropped INVITE is already the answer.
    def invite(self) -> str | None:
        """Send the INVITE and read until a final response, or nothing — which is an answer too."""
        self._socket.sendto(self._an_invite().encode(), (self._host, SIP_PORT))
        deadline = time.time() + ANSWER_TIMEOUT_S
        while time.time() < deadline:
            answer = self._read_one()
            if answer is None:
                continue
            first_line = answer.split("\r\n")[0]
            print(f"← {first_line}")
            if re.match(r"SIP/2\.0 [2-6]\d\d", first_line):
                return answer
        return None

    def acknowledge(self, answer: str) -> None:
        """The ACK that makes the dialogue real; the box opens the room on this, not on the 200."""
        self._remember_the_dialogue(answer)
        self._send("ACK", cseq=1)

    def hang_up(self) -> None:
        """The BYE, and the socket. A probe that leaves a call up bills the box for a call."""
        self._send("BYE", cseq=2)
        self._socket.close()

    def _an_invite(self) -> str:
        offer = "\r\n".join(
            [
                "v=0",
                f"o=probe 0 0 IN IP4 {self._local_address}",
                "s=probe",
                f"c=IN IP4 {self._local_address}",
                "t=0 0",
                f"m=audio {OFFERED_AUDIO_PORT} RTP/AVP 0 101",
                "a=rtpmap:0 PCMU/8000",
                "a=rtpmap:101 telephone-event/8000",
                "a=sendrecv",
                "",
            ]
        )
        return "\r\n".join(
            [
                f"INVITE {self._target} SIP/2.0",
                *self._headers("INVITE", cseq=1),
                f"Contact: <sip:probe@{self.where_it_calls_from}>",
                "Content-Type: application/sdp",
                f"Content-Length: {len(offer)}",
                "",
                offer,
            ]
        )

    def _send(self, method: str, cseq: int) -> None:
        message = "\r\n".join(
            [f"{method} {self._target} SIP/2.0", *self._headers(method, cseq), "", ""]
        )
        self._socket.sendto(message.encode(), (self._host, SIP_PORT))
        print(f"→ {method}")

    def _headers(self, method: str, cseq: int) -> list[str]:
        """What every message of this dialogue carries; the branch is new on each one."""
        to_tag = f";tag={self._remote_tag}" if self._remote_tag else ""
        return [
            f"Via: SIP/2.0/UDP {self.where_it_calls_from};branch=z9hG4bK{_token(16)};rport",
            "Max-Forwards: 70",
            f'From: "probe" <sip:{self._caller}@{self._domain}>;tag={self._tag}',
            f"To: <sip:{self._dialled}@{self._domain}>{to_tag}",
            f"Call-ID: {self._call_id}@{self._local_address}",
            f"CSeq: {cseq} {method}",
            "Content-Length: 0",
        ]

    def _remember_the_dialogue(self, answer: str) -> None:
        """The tag and the contact the box answered with: without them the ACK is a stray packet."""
        tag = re.search(r"^To:.*;tag=([^\r\n;]+)", answer, re.M | re.I)
        contact = re.search(r"^Contact:\s*<([^>]+)>", answer, re.M | re.I)
        self._remote_tag = tag.group(1) if tag else ""
        self._target = contact.group(1) if contact else self._target

    def _read_one(self) -> str | None:
        try:
            data, _ = self._socket.recvfrom(65535)
        except TimeoutError:
            return None
        return data.decode(errors="replace")


def _the_address_that_reaches(host: str) -> str:
    """Which of this machine's addresses the box will see — asked of the routing table, not DNS."""
    asking = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    asking.connect((host, SIP_PORT))
    address = str(asking.getsockname()[0])
    asking.close()
    return address


def _token(length: int) -> str:
    """A hex string for the one-shot identifiers a SIP transaction needs; nothing signs with it."""
    return "".join(random.choice("0123456789abcdef") for _ in range(length))
