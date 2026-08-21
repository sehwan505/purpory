CREATE TABLE context_decisions_next (
    id INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    input_text TEXT,
    proposal_json TEXT NOT NULL CHECK (json_valid(proposal_json)),
    final_action TEXT NOT NULL CHECK (final_action IN ('skip', 'retrieve', 'ask')),
    hints_json TEXT NOT NULL DEFAULT 'null' CHECK (json_valid(hints_json)),
    request_id INTEGER,
    model_id TEXT,
    model_revision TEXT,
    prompt_version TEXT NOT NULL,
    latency_ms INTEGER,
    fallback_reason TEXT,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (request_id) REFERENCES context_requests(id) ON DELETE SET NULL
) STRICT;

INSERT INTO context_decisions_next(
    id, project_id, session_id, input_hash, input_text, proposal_json, final_action,
    hints_json, request_id, model_id, model_revision, prompt_version, latency_ms,
    fallback_reason, created_at
)
SELECT
    id, project_id, session_id, input_hash, input_text, proposal_json, final_action,
    hints_json, request_id, model_id, model_revision, prompt_version, latency_ms,
    fallback_reason, created_at
FROM context_decisions;

CREATE TABLE gate_feedback_next (
    decision_id INTEGER PRIMARY KEY,
    verdict TEXT NOT NULL CHECK (verdict IN ('correct', 'incorrect')),
    expected_action TEXT CHECK (expected_action IN ('skip', 'retrieve', 'ask')),
    expected_keys_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(expected_keys_json)),
    note TEXT,
    created_at INTEGER NOT NULL DEFAULT (unixepoch()),
    FOREIGN KEY (decision_id) REFERENCES context_decisions_next(id) ON DELETE CASCADE
) STRICT;

INSERT INTO gate_feedback_next(decision_id, verdict, expected_action, expected_keys_json, note, created_at)
SELECT decision_id, verdict, expected_action, expected_keys_json, note, created_at
FROM gate_feedback;

DROP TABLE gate_feedback;
DROP TABLE context_decisions;
ALTER TABLE context_decisions_next RENAME TO context_decisions;
ALTER TABLE gate_feedback_next RENAME TO gate_feedback;

CREATE INDEX context_decisions_project_created ON context_decisions(project_id, created_at DESC, id DESC);
