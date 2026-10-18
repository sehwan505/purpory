import json
import os
import sys
import tempfile
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch


class MemoryProvider:
    pass


@dataclass
class RecallStatus:
    provider_label: str
    count: int
    glyph: str = "brain"


agent = types.ModuleType("agent")
memory_provider = types.ModuleType("agent.memory_provider")
memory_provider.MemoryProvider = MemoryProvider
memory_provider.RecallStatus = RecallStatus
sys.modules.setdefault("agent", agent)
sys.modules.setdefault("agent.memory_provider", memory_provider)

from purpory_hermes import PurporyMemoryProvider  # noqa: E402


class ProviderTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.addCleanup(self.root.cleanup)
        self.environment = patch.dict(os.environ, {"PURPORY_ROOT": self.root.name})
        self.executable = patch("purpory_hermes._executable", return_value="/bin/purpory")
        self.environment.start()
        self.executable.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(self.executable.stop)
        self.provider = PurporyMemoryProvider()
        self.provider.initialize("session-1", hermes_home=self.home.name, agent_context="primary")

    def test_prefetch_and_tools_use_purpory_cli(self):
        response = json.dumps(
            {"hookSpecificOutput": {"additionalContext": "[PURPORY MEMORY MAP]"}}
        )
        with patch.object(self.provider, "_run", return_value=response) as run:
            self.assertEqual(self.provider.prefetch("database"), "[PURPORY MEMORY MAP]")
            self.assertEqual(self.provider.recall_status().provider_label, "Purpory")
            payload = json.loads(run.call_args.args[1])
            self.assertEqual(payload["session_id"], "session-1")
            self.assertEqual(payload["cwd"], str(Path(self.root.name).resolve()))

        with patch.object(self.provider, "_run", return_value="node") as run:
            result = json.loads(
                self.provider.handle_tool_call("purpory_explain", {"nodes": ["knowledge:one"]})
            )
            self.assertEqual(result, {"result": "node"})
            self.assertEqual(run.call_args.args[0], ["explain", "--", "knowledge:one"])

    def test_session_end_records_status_without_a_transcript(self):
        with patch.object(self.provider, "_run", return_value="") as run:
            self.provider.on_session_end([{"role": "user", "content": "Remember SQLite."}])
            self.assertEqual(run.call_args.args[0], ["session-end", "hermes"])
            self.assertEqual(json.loads(run.call_args.args[1]), {
                "hook_event_name": "SessionEnd",
                "session_id": "session-1",
                "cwd": str(Path(self.root.name).resolve()),
            })
        self.assertEqual(list(Path(self.home.name).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
