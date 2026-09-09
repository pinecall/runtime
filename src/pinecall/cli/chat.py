"""`pinecall-runtime chat`: a text call from the terminal, the gateway proven with no browser."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import TextIO

import websockets
from websockets.exceptions import ConnectionClosed, InvalidStatus

from pinecall._settings import load_settings
from pinecall.cli.sessions.render import lines_of
from pinecall_protocol import decode_entry

PURPOSE: str = "a text call from the terminal: --agent <slug>, one line per turn"

DEFAULT_URL = "http://localhost:8080"
# The dev key is the only door /v1/chat has until ms-7 mints talk tokens, and it is read where
# every other setting is: a production key never opens this socket, so none is looked for.
NO_KEY = "set PINECALL_DEV_KEY: the chat socket opens to the dev key until ms-7 mints talk tokens"

# What the caller types is the caller's turn; what comes back is the call's own log, whole and
# unprojected, which is why the transcript is rendered by the very code `sessions show` uses.
PROMPT = "› "


def configure(parser: argparse.ArgumentParser) -> None:
    """One agent, one URL, one key: everything a call from a terminal needs to know."""
    parser.add_argument("--agent", required=True, help="the slug of a registered agent")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"default {DEFAULT_URL}")
    parser.add_argument("--caller", default=None, help="who is calling; minted when omitted")
    parser.set_defaults(run=run)


def run(arguments: argparse.Namespace) -> int:
    """Talk until stdin ends or the log says the call is over. Non-zero when nobody let us in."""
    key = load_settings().dev_key
    if not key:
        print(NO_KEY, file=sys.stderr)
        return 2
    try:
        return asyncio.run(talk(socket_url(arguments.url, arguments.agent, arguments.caller), key))
    except InvalidStatus as refused:
        print(
            f"the gateway refused the socket: HTTP {refused.response.status_code}", file=sys.stderr
        )
    except ConnectionClosed as closed:
        # 1008 with a reason is the gateway saying no by name; anything else is the call ending.
        print(
            f"\rthe gateway closed the call: {closed.rcvd or closed.sent or 'no close frame'}",
            file=sys.stderr,
        )
    except OSError as unreachable:
        print(f"no gateway at {arguments.url}: {unreachable}", file=sys.stderr)
    return 1


def socket_url(url: str, agent: str, caller: str | None) -> str:
    """The chat socket's address off the gateway's HTTP one: the scheme flips, the path is fixed."""
    base = url.rstrip("/").replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    who = f"&caller={caller}" if caller else ""
    return f"{base}/v1/chat?agent={agent}{who}"


async def talk(url: str, key: str, stdin: TextIO = sys.stdin, out: TextIO = sys.stdout) -> int:
    """Every line typed is one turn; every frame received is one entry, printed as it lands."""
    async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {key}"}) as ws:
        origin: float | None = None

        async def hearing() -> None:
            nonlocal origin
            async for frame in ws:
                entry = decode_entry(json.loads(frame))
                origin = entry.ts if origin is None else origin
                print("\r" + "\n".join(lines_of(entry, origin)), file=out)
                print(PROMPT, end="", file=out, flush=True)
                if entry.type == "call.ended":
                    return

        heard = asyncio.ensure_future(hearing())
        loop = asyncio.get_running_loop()
        while not heard.done():
            line = await loop.run_in_executor(None, stdin.readline)
            if not line:
                break
            if line.strip():
                await ws.send(json.dumps({"text": line.strip()}))
        if not heard.done():
            await ws.close()
            heard.cancel()
    return 0
