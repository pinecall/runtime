# pinecall

The Pinecall runtime as a distribution: the gateway and the worker, one wheel, one entrypoint
(`pinecall-runtime`). What it is, how it is deployed and how it is built are the repository's
pages two directories up — `README.md`, `ARCHITECTURE.md`, `docs/` — and the shapes it speaks are
`pinecall-core`, the distribution beside this one, which it depends on.

```
pip install pinecall                       the gateway
pip install pinecall[runtime]              the gateway and the worker, on the five tuned vendors
pip install pinecall[runtime,providers]    and the other forty livekit ships a plugin for
```
