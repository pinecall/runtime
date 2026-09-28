# pinecall

**Work in progress.** Rewriting it from scratch since Sunday, 2026-09-27.

Voice AI that runs where you do. Pinecall is a self-hosted runtime for voice agents: phone
calls, web voice, chat and WhatsApp, on your box, your carrier, your models.

The agent is an object. Its fields are what it remembers, its methods are what it can do, its
docstrings are the prompt, and `render()` is what it knows right now:

```tsx
import { Agent, tool } from "pinecall";

/** You are the front desk of a clinic. Short sentences: everything you say is read aloud. */
export default class FrontDesk extends Agent {
  patient?: Patient;
  slots: Slot[] = [];

  /** Free times on a day, for one specialty. */
  @tool()
  async freeSlots(day: string, specialty: string): Promise<Slot[]> {
    this.slots = await clinic.free(day, specialty);
    return this.slots;
  }

  render() {
    return (
      <>
        {!this.patient && <p>Ask for their name and phone number.</p>}
        {this.slots.length > 0 && <p>Offer two of these at most: {this.slots.join(", ")}.</p>}
      </>
    );
  }
}
```

The shape is borrowed from NVIDIA's [Object Oriented
Agents](https://github.com/NVIDIA-NeMo/labs-OO-Agents): state, capabilities and prompt in one class.
It runs on your own server with [pinecall](https://github.com/pinecall/agents) and is tested like
the rest of your software. The audio, the turn taking, the phone line, the log, the memory and the
judges are this repo's: the gateway and the worker, on [LiveKit](https://livekit.io), one wheel.

Why from scratch: v1 has been answering real calls for real customers in production. v2 is what
those calls taught us, written again to run anywhere: a cloud box, a rack in your office, a
network that talks to nobody. Your calls, your data, your models, and nothing leaves unless you
send it.

What you get from the rewrite:

- **Any vendor, no list.** Every LiveKit plugin works out of the box. Bring your own key, or use
  the box's. Nothing in the code decides which models you may run.
- **One deployment, two worlds.** Production and sandbox on one gateway and one database, with a
  worker fleet per world. One login, one key, a switch in the console.
- **Your agents do not change.** Same wire, same doors. Everything behind them is new.
- **A wheel, not a checkout.** A deploy is one file copied to the box and three units restarted.
- **Three hops from a door to its effect,** enforced by tests the commit hook runs. No registries,
  no layers.

Running today: the call's log, voice and text on one session, tenants and sign-in, the gateway
and the worker, numbers from any carrier account, outbound calls, WhatsApp. Coming: local models end to
end (the model, the ears and the voice on your own hardware), retrieval and memory, evals, the
operator CLI, the docs.

## Working on it

```
make check      the rules and the suites that need no database
make test       every suite, on a local Postgres in colima (T=tests/log for one folder)
make hooks      install the pre-commit hook (runs `make check`)
make deploy     the console built in, a wheel, released on the box, the live suite, the journal
```

Python 3.12 and `uv`; colima for the suites that need Postgres (`make db`). Nothing runs LiveKit
locally. `docs/the-environment.md` names every variable. Apache-2.0.
