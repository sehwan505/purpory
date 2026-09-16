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
bounded preflight hints, and queues the user/assistant transcript at the real
Hermes session boundary. Reconcile queued sessions from cron with a locally
authenticated agent CLI or Purpory's independent Codex OAuth provider:

```sh
purpory reconcile --executor codex
# or
purpory reconcile --executor claude
# or, after purpory model provider login openai-codex
purpory reconcile --executor openai-codex --model gpt-5.6-sol
```

The command uses Purpory's existing evidence-grounded validation and atomic
write path. For OAuth, select a global reconcile model or pass `--model MODEL`.
For Codex/Claude CLI, add `--model MODEL` only when their configured default
should not be used.
It deliberately does not mirror Hermes's agent-authored `MEMORY.md` writes into
Purpory durable memory.
