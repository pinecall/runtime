# pinecall

The Pinecall runtime's application: the gateway's doors, what a gateway holds open, the CLI
(`pinecall-runtime`) and the pages it serves. It depends on the eleven distributions the runtime is
built from, at its own version, so installing it installs the runtime. What it is, how it is
deployed and how it is built are the repository's pages two directories up — `README.md`,
`ARCHITECTURE.md`, `docs/`.

```
pip install pinecall                       the gateway
pip install pinecall[runtime]              the gateway and the worker, on the five tuned vendors
pip install pinecall[runtime,providers]    and the other forty livekit ships a plugin for
```
