# Purpory for Hermes Agent

This package implements Hermes Agent's official `MemoryProvider` interface by
calling the standalone Purpory CLI. Purpory remains a Go application; Python is
only the host-language adapter required by Hermes.

```sh
python -m pip install ./integrations/hermes
hermes config set memory.provider purpory
```

Run Hermes from a registered Purpory project. For gateways or other processes
started elsewhere, set the project root explicitly:

```sh
export PURPORY_ROOT=/path/to/project
```

If `purpory` is not on `PATH`, set `PURPORY_EXECUTABLE` to the executable path.
The provider supplies progressive `query`, `explain`, and `path` tools, injects
bounded preflight hints, and queues the user/assistant transcript for Purpory's
existing evidence-grounded reconciliation at the real Hermes session boundary.
It deliberately does not mirror Hermes's agent-authored `MEMORY.md` writes into
Purpory durable memory.
