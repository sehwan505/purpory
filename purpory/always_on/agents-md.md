## purpory

This project has a knowledge graph at purpory-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `purpory query "<question>"` when purpory-out/graph.json exists. Use `purpory path "<A>" "<B>"` for relationships and `purpory explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty purpory-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip purpory. Only skip purpory if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If purpory-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read purpory-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `purpory update .` to keep the graph current (AST-only, no API cost).
