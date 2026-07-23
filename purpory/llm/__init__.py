from __future__ import annotations

import json
import os
import re
import sys
import time

if "purpory.llm.helpers" in sys.modules:
    import importlib
    importlib.reload(sys.modules["purpory.llm.helpers"])

from concurrent.futures import ThreadPoolExecutor, as_completed

from pathlib import Path
from collections.abc import Callable

from purpory.file_slice import FileSlice, read_slice_text, unit_path, expand_oversized_files, bisect_slice

# Re-export necessary objects and helpers from helpers

from purpory.llm.helpers import (
    _EXTRACTION_SYSTEM,
    _MAX_IMAGE_BYTES,
    _MAX_IMAGES_PER_CHUNK,
    _IMAGE_TOKEN_ESTIMATE,
    _anthropic_content,
    _openai_content,
    _bedrock_content,

    BACKENDS,

    _PATH_IMAGE_BACKENDS,

    _FILE_CHAR_CAP,

    _PER_FILE_OVERHEAD_CHARS,

    _CHARS_PER_TOKEN,

    _TOKENIZER,

    _ImageRef,

    _custom_providers_path,

    provider_base_url_ok,

    _load_custom_providers,

    _resolve_max_tokens,

    _model_requires_default_temperature,

    _resolve_temperature,

    _bedrock_inference_config,

    _no_window_kwargs,

    _resolve_api_timeout,

    _resolve_max_retries,

    _thinking_disabled_via_env,

    _extraction_system,

    _file_to_text,

    _resolve_under_root,

    _neutralise_injection_sentinels,

    _wrap_untrusted,

    _read_files,

    _label_identifiers,

    _dispatched_source_text,

    _bind_node_evidence,

    _is_vision_image,

    _partition_semantic_files,

    _build_image_refs,

    _strip_pixels,

    _backend_supports_vision,

    _image_notes,

    _with_image_notes,

    _anthropic_content,

    _openai_content,

    _bedrock_content,

    _sanitize_fragment,

    _parse_llm_json,

    _response_is_hollow,

    _backend_env_keys,

    _get_backend_api_key,

    _format_backend_env_keys,

    _default_model_for_backend,

    _backend_pkg_hint,

    _estimate_file_tokens,

    _pack_chunks_by_tokens,

    _looks_like_context_exceeded,

    _mark_partial,

    _chunk_partial_files,

    _merged_partial_files,

    _partial_source_files,

    _strip_partial_markers,

    estimate_cost,

    _ollama_host_is_link_local_or_metadata,

    _validate_ollama_base_url,

    detect_backend,








    _LABEL_FENCE_RE,
    _LABEL_MAX_COMMUNITIES,
    _LABEL_TOP_K,
    _LABEL_MAXLEN,
    _LABEL_BATCH_SIZE,
)


def extract_files_direct(
    files: list[Path],
    backend: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    root: Path = Path("."),
    *,
    deep_mode: bool = False,
) -> dict:
    """Extract semantic nodes/edges from a list of files using the given backend adapter."""
    files = [f if isinstance(f, (Path, FileSlice)) else Path(f) for f in files]
    if backend is None:
        backend = detect_backend()
        if backend is None:
            raise ValueError(
                "No LLM backend configured. Set one of: GEMINI_API_KEY, ANTHROPIC_API_KEY, "
                "OPENAI_API_KEY, DEEPSEEK_API_KEY, MOONSHOT_API_KEY, "
                "AZURE_OPENAI_API_KEY+AZURE_OPENAI_ENDPOINT, OLLAMA_BASE_URL, "
                "or AWS credentials. Pass backend= explicitly to select a provider."
            )
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend {backend!r}. Available: {sorted(BACKENDS)}")

    cfg = BACKENDS[backend]
    key = api_key or _get_backend_api_key(backend)
    if not key and backend == "ollama":
        ollama_url = os.environ.get("OLLAMA_BASE_URL", cfg.get("base_url", ""))
        _validate_ollama_base_url(ollama_url)
        print(
            "[purpory] WARNING: ollama backend selected with no OLLAMA_API_KEY set; "
            f"sending corpus to {ollama_url}. Set OLLAMA_API_KEY (any non-empty value) "
            "to suppress this warning.",
            file=sys.stderr,
        )
        key = "ollama"
    if not key and backend not in ("bedrock", "claude-cli"):
        raise ValueError(
            f"No API key for backend '{backend}'. "
            f"Set {_format_backend_env_keys(backend)} or pass api_key=."
        )
    mdl = model or _default_model_for_backend(backend)
    text_files, image_files = _partition_semantic_files(files)
    user_msg = _read_files(text_files, root)
    vision = _backend_supports_vision(backend)
    read_bytes = vision and backend not in _PATH_IMAGE_BACKENDS
    image_refs = _build_image_refs(image_files, root, read_bytes=read_bytes) if image_files else []
    if image_refs and not vision:
        image_refs = _strip_pixels(image_refs)
    max_out = _resolve_max_tokens(cfg.get("max_tokens", 8192))

    if backend == "claude":
        result = _call_claude(key, mdl, user_msg, max_tokens=max_out, deep_mode=deep_mode, images=image_refs)
    elif backend == "claude-cli":
        result = _call_claude_cli(user_msg, max_tokens=max_out, deep_mode=deep_mode, images=image_refs)
    elif backend == "bedrock":
        result = _call_bedrock(mdl, user_msg, max_tokens=max_out, deep_mode=deep_mode, images=image_refs)
    elif backend == "azure":
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        result = _call_azure(key, endpoint, mdl, user_msg, temperature=_resolve_temperature(cfg.get("temperature", 0), mdl), max_tokens=max_out, deep_mode=deep_mode)
    else:
        result = _call_openai_compat(
            cfg["base_url"],
            key,
            mdl,
            user_msg,
            temperature=_resolve_temperature(cfg.get("temperature", 0), mdl),
            reasoning_effort=cfg.get("reasoning_effort"),
            max_completion_tokens=_resolve_max_tokens(
                cfg.get("max_completion_tokens") or cfg.get("max_tokens", 8192)
            ),
            backend=backend,
            deep_mode=deep_mode,
            images=image_refs,
            extra_body=cfg.get("extra_body"),
        )

    if isinstance(result, dict):
        try:
            _n_unverified = _bind_node_evidence(result, text_files, root)
            if _n_unverified:
                print(
                    f"[purpory] {_n_unverified} semantic node(s) had no evidence in "
                    "the source and were flagged verification=unverified",
                    file=sys.stderr,
                )
        except Exception as _exc:  # advisory
            print(f"[purpory] evidence-binding skipped: {_exc}", file=sys.stderr)
    return result



def _call_llm(
    prompt: str,
    *,
    backend: str,
    max_tokens: int = 200,
    model: str | None = None,
    usage_out: dict | None = None,
) -> str:
    """Send a plain-text prompt to backend adapter and return the model's text reply."""
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend {backend!r}")
    cfg = BACKENDS[backend]
    key = _get_backend_api_key(backend)
    if not key and backend == "ollama":
        ollama_url = os.environ.get("OLLAMA_BASE_URL", cfg.get("base_url", ""))
        _validate_ollama_base_url(ollama_url)
        key = "ollama"
    if not key and backend not in ("bedrock", "claude-cli"):
        raise ValueError(
            f"No API key for backend '{backend}'. Set {_format_backend_env_keys(backend)}."
        )
    mdl = model or _default_model_for_backend(backend)

    from purpory.llm.providers import get_provider
    provider = get_provider(backend)
    provider_cfg = dict(cfg, name=backend)
    return provider.call_raw(
        api_key=key,
        model=mdl,
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=_resolve_temperature(cfg.get("temperature", 0), mdl),
        cfg=provider_cfg,
        usage_out=usage_out,
    )


def _extract_with_adaptive_retry(
    chunk: list[Path],
    backend: str,
    api_key: str | None,
    model: str | None,
    root: Path,
    max_depth: int,
    _depth: int = 0,
    *,
    deep_mode: bool = False,
) -> dict:
    """Extract a chunk; if the response is truncated (`finish_reason="length"`)
    or the API rejects the prompt as too large for the model's context window,
    split the chunk in half and recurse.

    Three signals drive the retry, all funnelled through the same code:

    - `finish_reason == "length"` — the model accepted the input but ran out of
      `max_completion_tokens` mid-output. The truncated JSON is unparseable, so
      we discard it and re-extract on smaller inputs that produce shorter
      outputs.

    - context-window-exceeded API errors — the model rejected the input
      outright (HTTP 400 from LM Studio, llama.cpp, vLLM, OpenAI, etc.).
      Without a retry the whole chunk would fail with no output. Splitting in
      half is the same recovery as for the `length` case and works for the
      same reason.

    - hollow successful responses — the model returned HTTP 200 with empty,
      null, or unparseable content (typical of a local Ollama under load).
      `_call_openai_compat` re-labels these as `finish_reason="length"` so they
      take the same recovery path; without that the chunk would be silently
      dropped from the corpus.

    Recursion is capped at `max_depth` to bound worst-case cost. A chunk of N
    files can split into up to 2**max_depth pieces — at depth=3 that's 8x. If
    still failing at the cap, we surface the (likely empty) result with a
    warning rather than infinite-loop.

    A single-file chunk that overflows is recoverable only when it's a slice of
    a splittable document: the slice is bisected and retried (#1369). A whole
    non-splittable file (e.g. one huge code file) can't be made smaller than
    itself, so we return what we got and warn.
    """
    def _merge_two(left_units, right_units) -> dict:
        left = _extract_with_adaptive_retry(
            left_units, backend, api_key, model, root, max_depth, _depth + 1, deep_mode=deep_mode
        )
        right = _extract_with_adaptive_retry(
            right_units, backend, api_key, model, root, max_depth, _depth + 1, deep_mode=deep_mode
        )
        return {
            "nodes": left.get("nodes", []) + right.get("nodes", []),
            "edges": left.get("edges", []) + right.get("edges", []),
            "hyperedges": left.get("hyperedges", []) + right.get("hyperedges", []),
            "input_tokens": left.get("input_tokens", 0) + right.get("input_tokens", 0),
            "output_tokens": left.get("output_tokens", 0) + right.get("output_tokens", 0),
            "model": model,
            "finish_reason": "stop",
            "_partial_files": _merged_partial_files(left, right),
        }

    def _split_lone_slice() -> "tuple[FileSlice, FileSlice] | None":
        # When a single-unit chunk is a slice, bisect the slice so we can retry
        # on a smaller range rather than give up (#1369).
        if len(chunk) == 1 and isinstance(chunk[0], FileSlice) and _depth < max_depth:
            return bisect_slice(chunk[0])
        return None

    try:
        result = extract_files_direct(
            chunk, backend=backend, api_key=api_key, model=model, root=root, deep_mode=deep_mode
        )
    except Exception as exc:  # noqa: BLE001 — re-raise unless it's a known context overflow
        if not _looks_like_context_exceeded(exc):
            raise
        if len(chunk) <= 1:
            halves = _split_lone_slice()
            if halves is not None:
                print(
                    f"[purpory] slice of {unit_path(chunk[0])} exceeded context at "
                    f"depth {_depth}; splitting the slice and retrying",
                    file=sys.stderr,
                )
                return _merge_two([halves[0]], [halves[1]])
            print(
                f"[purpory] single-file chunk {unit_path(chunk[0])} exceeds model context "
                f"and cannot be split further: {exc}",
                file=sys.stderr,
            )
            return {"nodes": [], "edges": [], "hyperedges": [], "input_tokens": 0, "output_tokens": 0, "model": model, "finish_reason": "stop"}
        if _depth >= max_depth:
            print(
                f"[purpory] chunk of {len(chunk)} still overflows context at "
                f"recursion depth {_depth} (max {max_depth}) — dropping",
                file=sys.stderr,
            )
            return {"nodes": [], "edges": [], "hyperedges": [], "input_tokens": 0, "output_tokens": 0, "model": model, "finish_reason": "stop"}
        print(
            f"[purpory] chunk of {len(chunk)} exceeded context at depth "
            f"{_depth} ({type(exc).__name__}); splitting in half and retrying",
            file=sys.stderr,
        )
        mid = len(chunk) // 2
        left = _extract_with_adaptive_retry(
            chunk[:mid], backend, api_key, model, root, max_depth, _depth + 1, deep_mode=deep_mode
        )
        right = _extract_with_adaptive_retry(
            chunk[mid:], backend, api_key, model, root, max_depth, _depth + 1, deep_mode=deep_mode
        )
        return {
            "nodes": left.get("nodes", []) + right.get("nodes", []),
            "edges": left.get("edges", []) + right.get("edges", []),
            "hyperedges": left.get("hyperedges", []) + right.get("hyperedges", []),
            "input_tokens": left.get("input_tokens", 0) + right.get("input_tokens", 0),
            "output_tokens": left.get("output_tokens", 0) + right.get("output_tokens", 0),
            "model": model,
            "finish_reason": "stop",
            "_partial_files": _merged_partial_files(left, right),
        }

    if result.get("finish_reason") != "length":
        return result

    if len(chunk) <= 1:
        halves = _split_lone_slice()
        if halves is not None:
            print(
                f"[purpory] slice of {unit_path(chunk[0])} truncated at depth {_depth}; "
                f"splitting the slice and retrying",
                file=sys.stderr,
            )
            return _merge_two([halves[0]], [halves[1]])
        print(
            f"[purpory] single-file chunk {unit_path(chunk[0])} truncated at "
            f"max_completion_tokens — partial result kept (not cached as complete)",
            file=sys.stderr,
        )
        # The node set is incomplete; mark it so it is not promoted to the
        # semantic cache as authoritative and is re-dispatched next run. Also
        # record the chunk's files so a truncation that parsed to nothing (an
        # empty item set) still marks the file partial (#1950 empty-parse gap).
        _mark_partial(result)
        result["_partial_files"] = sorted(
            set(_chunk_partial_files(chunk)) | set(result.get("_partial_files", []) or [])
        )
        return result

    if _depth >= max_depth:
        print(
            f"[purpory] chunk of {len(chunk)} still truncated at recursion "
            f"depth {_depth} (max {max_depth}) — partial result kept (not cached as complete)",
            file=sys.stderr,
        )
        # Conservative: this marks every file in the merged chunk partial, even
        # ones that finished cleanly during recursion. Over-marking only costs a
        # re-extraction next run; under-marking would serve a truncated file as
        # complete, so err toward re-extraction.
        _mark_partial(result)
        result["_partial_files"] = sorted(
            set(_chunk_partial_files(chunk)) | set(result.get("_partial_files", []) or [])
        )
        return result

    print(
        f"[purpory] chunk of {len(chunk)} truncated at depth {_depth}, "
        f"splitting into halves of {len(chunk) // 2} and "
        f"{len(chunk) - len(chunk) // 2}",
        file=sys.stderr,
    )
    mid = len(chunk) // 2
    left = _extract_with_adaptive_retry(
        chunk[:mid], backend, api_key, model, root, max_depth, _depth + 1, deep_mode=deep_mode
    )
    right = _extract_with_adaptive_retry(
        chunk[mid:], backend, api_key, model, root, max_depth, _depth + 1, deep_mode=deep_mode
    )

    return {
        "nodes": left.get("nodes", []) + right.get("nodes", []),
        "edges": left.get("edges", []) + right.get("edges", []),
        "hyperedges": left.get("hyperedges", []) + right.get("hyperedges", []),
        "input_tokens": left.get("input_tokens", 0) + right.get("input_tokens", 0),
        "output_tokens": left.get("output_tokens", 0) + right.get("output_tokens", 0),
        "model": result.get("model"),
        # Both halves either succeeded or have already surfaced their own
        # truncation warning; the merged result is no longer truncated as a
        # logical unit.
        "finish_reason": "stop",
        "_partial_files": _merged_partial_files(left, right),
    }

def extract_corpus_parallel(
    files: list[Path],
    backend: str = "kimi",
    api_key: str | None = None,
    model: str | None = None,
    root: Path = Path("."),
    chunk_size: int = 20,
    on_chunk_done: Callable | None = None,
    token_budget: int | None = 60_000,
    max_concurrency: int = 4,
    max_retry_depth: int = 3,
    deep_mode: bool = False,
) -> dict:
    """Extract a corpus in chunks, merging results.

    Chunking strategy:
        - If `token_budget` is set (default 60_000), files are packed to fit
          the budget and grouped by parent directory. This avoids the worst
          case where 20 randomly-grouped files exceed a model's context
          window in a single request.
        - If `token_budget=None`, falls back to the legacy fixed-count
          `chunk_size` packing for backwards compatibility.

    Concurrency:
        - Chunks run in parallel via a thread pool capped at `max_concurrency`
          (default 4 — conservative to stay under provider rate limits).
        - Set `max_concurrency=1` to force sequential execution.

    Adaptive retry on truncation:
        - When the LLM returns `finish_reason="length"` (output truncated at
          `max_completion_tokens`), the chunk is split in half and each half
          re-extracted recursively, up to `max_retry_depth` levels deep
          (default 3 → max 8x expansion of one chunk).
        - This is signal-driven: chunks too dense to fit in one response
          self-heal by splitting until they do, while well-sized chunks pay
          no extra cost. Set `max_retry_depth=0` to disable retries.

    `on_chunk_done(idx, total, chunk_result)` fires once per chunk as it
    completes (in completion order, not submission order). `idx` is the
    chunk's submission index so callers can correlate progress. The
    callback fires once per top-level chunk; recursive splits are merged
    transparently before the callback is invoked.

    Returns merged dict with nodes, edges, hyperedges, input_tokens,
    output_tokens. Failed chunks are logged to stderr and skipped — one bad
    chunk does not abort the run.

    Accepts ``str`` paths as well as ``Path``; string entries are coerced up
    front so packing/slicing helpers can rely on ``Path`` semantics (#1386).
    """
    files = [f if isinstance(f, (Path, FileSlice)) else Path(f) for f in files]
    # Split oversized splittable documents into slices that cover the whole file
    # before packing, so content past _FILE_CHAR_CAP is extracted instead of
    # silently dropped (#1369). Files at/under the cap pass through unchanged.
    files = expand_oversized_files(files, _FILE_CHAR_CAP)
    if token_budget is not None:
        chunks = _pack_chunks_by_tokens(files, token_budget=token_budget)
    else:
        chunks = [files[i:i + chunk_size] for i in range(0, len(files), chunk_size)]

    merged: dict = {
        "nodes": [], "edges": [], "hyperedges": [],
        "input_tokens": 0, "output_tokens": 0,
        "failed_chunks": 0,  # count of chunks that raised — loud failure on chunk errors
    }
    total = len(chunks)

    def _run_one(idx: int, chunk: list[Path]) -> tuple[int, dict | None, Exception | None]:
        t0 = time.time()
        try:
            result = _extract_with_adaptive_retry(
                chunk,
                backend=backend,
                api_key=api_key,
                model=model,
                root=root,
                max_depth=max_retry_depth,
                deep_mode=deep_mode,
            )
            result["elapsed_seconds"] = round(time.time() - t0, 2)
            return idx, result, None
        except Exception as exc:  # noqa: BLE001 — caller-facing surface, log + continue
            return idx, None, exc

    # Ollama serves one request at a time per loaded model on a single GPU.
    # Four concurrent 60k-token requests cause VRAM pressure and hollow
    # responses after 3-4 chunks (#798). Force serial unless the user opts in.
    if backend == "ollama" and os.environ.get("PURPORY_OLLAMA_PARALLEL", "").strip() != "1":
        max_concurrency = 1
    # claude-cli shells out to a Claude Code session; parallel subprocesses conflict
    # over session state. Force serial unless the user explicitly opts in.
    if backend == "claude-cli" and os.environ.get("PURPORY_CLAUDE_CLI_PARALLEL", "").strip() != "1":
        max_concurrency = 1
    def _checkpoint_chunk(result: dict, chunk: "list[Path | FileSlice]") -> None:
        # Persist each chunk's semantic results to the cache as soon as it
        # completes. Without this, the semantic cache is only written once, at
        # the very end of the run (in __main__), so a run interrupted partway
        # — a crash, a kill, or a claude-cli/API run that exits on a rate
        # limit — loses every completed chunk and restarts from scratch. This
        # is best-effort: a cache write failure must never abort extraction.
        if os.environ.get("PURPORY_NO_INCREMENTAL_CACHE"):
            return
        try:
            from purpory.cache import save_semantic_cache as _scs
            # Scope the write to the files actually dispatched in this chunk
            # (#1757). The model can attribute a node's source_file to another
            # corpus file; without this bound, that stray node would clobber the
            # other file's complete cache entry (or, with merge_existing, pollute
            # it). Use unit_path so a FileSlice (one slice of an oversized doc)
            # resolves to its parent file; a bare Path passes through. (#1870: the
            # old `.rel` attribute does not exist on FileSlice, so every sliced
            # chunk leaked the FileSlice object into the allowlist and the write
            # raised TypeError, silently defeating the checkpoint.)
            allowed = [unit_path(item) for item in chunk]
            # Deep-mode results checkpoint into their own namespace
            # (cache/semantic-deep/) so a deep run never overwrites standard
            # entries — and a later standard run never serves deep ones (#1894).
            _scs(
                result.get("nodes", []),
                result.get("edges", []),
                result.get("hyperedges", []),
                root=root,
                merge_existing=True,
                allowed_source_files=allowed,
                mode="deep" if deep_mode else None,
                # Stamp the entry with the prompt that produced it, so a release
                # that changes _EXTRACTION_SYSTEM re-extracts instead of replaying
                # this vintage forever (#1939).
                prompt=_extraction_system(deep=deep_mode),
                # A truncated/partial chunk must not be checkpointed as
                # authoritative: pass the partial file set so its entry is
                # stamped ``partial: True`` and re-dispatched next run.
                partial_source_files=_partial_source_files(result) or None,
            )
        except Exception as _exc:  # noqa: BLE001 — checkpoint is best-effort
            print(f"[purpory] incremental cache checkpoint failed: {_exc}", file=sys.stderr)

    workers = max(1, min(max_concurrency, total))
    if workers == 1:
        # Avoid thread pool overhead for single-worker runs (and keep
        # callback ordering identical to the pre-refactor sequential path).
        for idx, chunk in enumerate(chunks):
            _, result, exc = _run_one(idx, chunk)
            if exc is not None:
                print(f"[purpory] chunk {idx + 1}/{total} failed: {exc}", file=sys.stderr)
                merged["failed_chunks"] += 1
                continue
            assert result is not None
            _merge_into(merged, result)
            _checkpoint_chunk(result, chunk)
            if callable(on_chunk_done):
                on_chunk_done(idx, total, result)
    else:
        # Merge in deterministic submission order, NOT completion order. Merging
        # as chunks finish makes the node/edge ordering in the returned corpus
        # (and therefore graph.json) depend on which network call happened to
        # return first — so identical input churned run-to-run (#1632). Collect
        # results keyed by chunk index and merge in sorted order after the pool
        # drains; this matches the serial path's order. The progress callback
        # still fires in completion order so long local runs aren't silent.
        results_by_idx: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_run_one, idx, chunk) for idx, chunk in enumerate(chunks)]
            for future in as_completed(futures):
                idx, result, exc = future.result()
                if exc is not None:
                    print(
                        f"[purpory] chunk {idx + 1}/{total} failed: {exc}",
                        file=sys.stderr,
                    )
                    merged["failed_chunks"] += 1
                    continue
                assert result is not None
                results_by_idx[idx] = result
                _checkpoint_chunk(result, chunks[idx])
                if callable(on_chunk_done):
                    on_chunk_done(idx, total, result)
        for idx in sorted(results_by_idx):
            _merge_into(merged, results_by_idx[idx])

    # Loud failure summary — surface chunk failures at end so they're never
    # buried mid-log. Exit 0 preserved for caller compatibility; the
    # summary block makes the problem visible.
    if merged["failed_chunks"] > 0:
        print(
            f"[purpory] WARNING: {merged['failed_chunks']}/{total} semantic chunk(s) failed"
            " — see errors above. Partial results returned.",
            file=sys.stderr,
        )

    # Dispatch/return reconciliation (#1890). A chunk can return a clean, non-empty
    # response that simply omits some of the documents it was given; those docs then
    # vanish from the graph with no node, no warning, and no cache/manifest stamp, so
    # they are silently re-dispatched (and re-omitted) forever. Diff the files we
    # dispatched against the source_files that actually came back and surface the gap.
    dispatched = {unit_path(f) for chunk in chunks for f in chunk}

    # Out-of-scope node filter (#1895). The #1757 cache guard already refuses
    # to WRITE a cache entry for a node whose source_file is a real file that
    # was not dispatched, but the node itself still flowed into the merged
    # result and landed in graph.json. Mirror the #1757 condition here: resolve
    # each source_file against root and drop the node only when it resolves to
    # an existing file (.is_file()) outside the dispatched set — non-file
    # source_files (concepts, model-invented anchors) pass through untouched.
    # Runs BEFORE the #1890 covered/uncovered reconciliation so that diff
    # reflects the post-filter graph.
    def _resolve_against_root(value: "str | Path") -> Path:
        p = Path(value)
        if not p.is_absolute():
            p = root / p
        try:
            return p.resolve()
        except (OSError, RuntimeError):
            return p

    _dispatched_resolved = {_resolve_against_root(p) for p in dispatched}

    def _out_of_scope(item: dict) -> bool:
        sf = item.get("source_file")
        if not sf:
            return False
        p = _resolve_against_root(sf)
        return p.is_file() and p not in _dispatched_resolved

    dropped_ids: set = set()
    dropped_files: set[str] = set()
    kept_nodes: list[dict] = []
    for n in merged.get("nodes", []):
        if _out_of_scope(n):
            if n.get("id") is not None:
                dropped_ids.add(n.get("id"))
            dropped_files.add(str(n.get("source_file")))
            continue
        kept_nodes.append(n)
    dropped_node_count = len(merged.get("nodes", [])) - len(kept_nodes)
    merged["out_of_scope_dropped"] = dropped_node_count
    if dropped_node_count:
        merged["nodes"] = kept_nodes
        # Keep the graph consistent: an edge or hyperedge referencing a
        # dropped node's id (or itself attributed to an undispatched real
        # file) must not survive its endpoint.
        merged["edges"] = [
            e for e in merged.get("edges", [])
            if not _out_of_scope(e)
            and e.get("source") not in dropped_ids
            and e.get("target") not in dropped_ids
        ]
        merged["hyperedges"] = [
            h for h in merged.get("hyperedges", [])
            if not _out_of_scope(h)
            and not (dropped_ids & set(h.get("nodes", []) or []))
        ]
        shown = ", ".join(sorted(Path(f).name for f in dropped_files)[:5])
        more = f" (+{len(dropped_files) - 5} more)" if len(dropped_files) > 5 else ""
        print(
            f"[purpory] WARNING: dropped {dropped_node_count} out-of-scope node(s) "
            f"attributed to file(s) not dispatched for extraction: {shown}{more}. "
            "The model mis-attributed them to another corpus file; they were "
            "excluded from the graph (#1895).",
            file=sys.stderr,
        )

    covered: set[Path] = set()
    for n in merged.get("nodes", []):
        sf = n.get("source_file")
        if sf:
            p = Path(sf)
            covered.add(p if p.is_absolute() else (root / p))
    uncovered = sorted(
        p for p in dispatched
        if p.resolve() not in {c.resolve() for c in covered}
    )
    merged["uncovered_files"] = [str(p) for p in uncovered]
    if uncovered:
        shown = ", ".join(p.name for p in uncovered[:5])
        more = f" (+{len(uncovered) - 5} more)" if len(uncovered) > 5 else ""
        print(
            f"[purpory] WARNING: {len(uncovered)}/{len(dispatched)} dispatched file(s) "
            f"produced no nodes and are absent from the graph: {shown}{more}. The model "
            "returned a response but omitted them; a re-run will retry them.",
            file=sys.stderr,
        )
    return merged

def _merge_into(merged: dict, result: dict) -> None:
    """Append a chunk result into the running merged accumulator."""
    merged["nodes"].extend(result.get("nodes", []))
    merged["edges"].extend(result.get("edges", []))
    merged["hyperedges"].extend(result.get("hyperedges", []))
    merged["input_tokens"] += result.get("input_tokens", 0)
    merged["output_tokens"] += result.get("output_tokens", 0)
    # Carry forward files a chunk truncated to an empty parse (#1950): these have
    # no items to ride the merge, so they'd otherwise be lost from the run-level
    # partial set the manifest stamp consults.
    incoming = result.get("_partial_files")
    if incoming:
        merged["_partial_files"] = sorted(
            set(merged.get("_partial_files", []) or []) | set(incoming)
        )

def label_communities(
    G,
    communities,
    *,
    backend: str,
    model: str | None = None,
    gods=None,
    max_communities: int | None = None,
    top_k: int = _LABEL_TOP_K,
    batch_size: int = _LABEL_BATCH_SIZE,
    max_concurrency: int = 4,
    usage_out: dict | None = None,
) -> dict[int, str]:
    """Return a complete ``{cid: name}`` map using ``backend`` for naming.

    Communities are labeled in batches of ``batch_size`` so the prompt fits in a
    16k-token context window (which is enough for one batch of ~100 communities
    × ``top_k`` node labels). With the previous hard cap of 200 communities in a
    single call, self-hosted 16k models (Qwen3, Llama 3.1 8B-Instruct, etc.)
    routinely overflowed context and dropped the entire labeling pass to
    placeholders.

    ``max_communities=None`` (the default) labels every community. Pass an
    integer to cap the total (the legacy 200 default preserved this behavior;
    explicit callers can still pin it). Placeholders (``Community N``) are used
    for any community the backend did not name. Per-batch failures are logged
    to stderr and skipped — the surviving batches still contribute labels.

    Raises on the first batch's backend/parse failure if it leaves *no* labels
    written. Callers that want graceful degradation should use
    :func:`generate_community_labels`.
    """
    labels = _placeholder_community_labels(communities)
    cap = len(communities) if max_communities is None else max_communities
    lines, labeled_cids = _community_label_lines(G, communities, gods, cap, top_k)
    if not lines:
        return labels

    n_batches = (len(labeled_cids) + batch_size - 1) // batch_size

    # Mirror extract_corpus_parallel's backend guards: Ollama serves one request at
    # a time per loaded model (parallel batches cause VRAM pressure and hollow
    # replies, #798) and claude-cli shells out to a single Claude Code session that
    # parallel subprocesses corrupt. Force serial for these unless the user opts in
    # via the same env switches.
    if backend == "ollama" and os.environ.get("PURPORY_OLLAMA_PARALLEL", "").strip() != "1":
        max_concurrency = 1
    if backend == "claude-cli" and os.environ.get("PURPORY_CLAUDE_CLI_PARALLEL", "").strip() != "1":
        max_concurrency = 1
    workers = max(1, min(max_concurrency, n_batches))

    def _run_batch(batch_idx: int):
        start = batch_idx * batch_size
        end = min(start + batch_size, len(labeled_cids))
        # Accumulate token usage into a per-batch dict so concurrent workers
        # never race on the shared accumulator; it is merged on the main thread
        # in _merge (#1694).
        batch_usage: dict = {} if usage_out is not None else None
        batch_kwargs = {"usage_out": batch_usage} if usage_out is not None else {}
        try:
            parsed = _label_batch_with_retry(
                labeled_cids[start:end], lines[start:end], backend=backend, model=model,
                **batch_kwargs,
            )
            return batch_idx, parsed, None, batch_usage
        except Exception as exc:  # noqa: BLE001 - reported per-batch; surfaced below
            return batch_idx, None, exc, batch_usage

    written = 0
    errors: dict[int, Exception] = {}

    def _merge(batch_idx: int, parsed, exc, batch_usage=None) -> None:
        nonlocal written
        # Count tokens even for a failed batch: the LLM call was billed whether
        # or not the reply parsed.
        if usage_out is not None and batch_usage:
            usage_out["input"] = usage_out.get("input", 0) + batch_usage.get("input", 0)
            usage_out["output"] = usage_out.get("output", 0) + batch_usage.get("output", 0)
        if exc is not None:
            errors[batch_idx] = exc
            start = batch_idx * batch_size
            end = min(start + batch_size, len(labeled_cids))
            print(
                f"[purpory label] batch {batch_idx + 1}/{n_batches} "
                f"({end - start} communities) failed: {exc}",
                file=sys.stderr,
            )
            return
        labels.update(parsed)
        written += len(parsed)

    # Fan out batches; merge on the main thread so `labels` is never mutated
    # concurrently. workers == 1 keeps the original sequential path verbatim.
    if workers == 1:
        for batch_idx in range(n_batches):
            _merge(*_run_batch(batch_idx))
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_run_batch, b) for b in range(n_batches)]
            for future in as_completed(futures):
                _merge(*future.result())

    if written == 0 and errors:
        # Every batch failed; propagate the lowest-index error so the message is
        # deterministic and generate_community_labels degrades cleanly.
        raise errors[min(errors)]
    return labels

def generate_community_labels(
    G,
    communities,
    *,
    backend: str | None = None,
    model: str | None = None,
    gods=None,
    quiet: bool = False,
    max_concurrency: int = 4,
    batch_size: int = _LABEL_BATCH_SIZE,
    usage_out: dict | None = None,
) -> tuple[dict[int, str], str]:
    """CLI entry point: resolve a backend, name communities, and degrade to
    ``Community N`` placeholders on any failure (no backend, API error, malformed
    reply). Returns ``(labels, source)`` where source is ``"llm"`` or
    ``"placeholder"``. Never raises."""
    if backend is None:
        try:
            backend = detect_backend()
        except Exception:
            backend = None
    if not backend:
        if not quiet:
            print(
                "[purpory label] no LLM backend configured; keeping Community N "
                "placeholders. Set an API key (e.g. GOOGLE_API_KEY) or pass --backend.",
                file=sys.stderr,
            )
        return _placeholder_community_labels(communities), "placeholder"
    try:
        labels = label_communities(
            G, communities, backend=backend, model=model, gods=gods,
            max_concurrency=max_concurrency, batch_size=batch_size,
            usage_out=usage_out,
        )
        return labels, "llm"
    except Exception as exc:
        if not quiet:
            print(
                f"[purpory label] warning: community labeling failed ({exc}); "
                "using Community N placeholders.",
                file=sys.stderr,
            )
        return _placeholder_community_labels(communities), "placeholder"


# Legacy unit test compatibility wrappers

def _call_openai_compat(
    base_url: str,
    api_key: str,
    model: str,
    user_message: str,
    temperature: float | None = 0,
    reasoning_effort: str | None = None,
    max_completion_tokens: int = 8192,
    *,
    backend: str = "",
    deep_mode: bool = False,
    images: list | None = None,
    extra_body: dict | None = None,
) -> dict:
    from purpory.llm.providers.openai import OpenAICompatProvider
    provider = OpenAICompatProvider()
    cfg = {"base_url": base_url, "name": backend, "reasoning_effort": reasoning_effort, "extra_body": extra_body}
    return provider.call_direct(
        api_key=api_key,
        model=model,
        user_message=user_message,
        max_tokens=max_completion_tokens,
        temperature=temperature,
        deep_mode=deep_mode,
        images=images,
        cfg=cfg,
    )

def _call_claude(
    api_key: str,
    model: str,
    user_message: str,
    max_tokens: int = 8192,
    *,
    deep_mode: bool = False,
    images: list | None = None,
) -> dict:
    from purpory.llm.providers.anthropic import AnthropicProvider
    provider = AnthropicProvider()
    return provider.call_direct(
        api_key=api_key,
        model=model,
        user_message=user_message,
        max_tokens=max_tokens,
        temperature=None,
        deep_mode=deep_mode,
        images=images,
    )

def _call_claude_cli(
    user_message: str,
    max_tokens: int = 8192,
    *,
    deep_mode: bool = False,
    images: list | None = None,
) -> dict:
    from purpory.llm.providers.claude_cli import ClaudeCLIProvider
    provider = ClaudeCLIProvider()
    return provider.call_direct(
        api_key=None,
        model="claude-code-plan",
        user_message=user_message,
        max_tokens=max_tokens,
        temperature=0,
        deep_mode=deep_mode,
        images=images,
    )

def _call_azure(
    api_key: str,
    endpoint: str,
    model: str,
    user_message: str,
    temperature: float | None = 0,
    max_tokens: int = 8192,
    *,
    deep_mode: bool = False,
) -> dict:
    from purpory.llm.providers.azure import AzureProvider
    provider = AzureProvider()
    return provider.call_direct(
        api_key=api_key,
        model=model,
        user_message=user_message,
        max_tokens=max_tokens,
        temperature=temperature,
        deep_mode=deep_mode,
        cfg={"endpoint": endpoint},
    )

def _call_bedrock(
    model: str,
    user_message: str,
    max_tokens: int = 8192,
    *,
    deep_mode: bool = False,
    images: list | None = None,
) -> dict:
    from purpory.llm.providers.bedrock import BedrockProvider
    provider = BedrockProvider()
    return provider.call_direct(
        api_key=None,
        model=model,
        user_message=user_message,
        max_tokens=max_tokens,
        temperature=0,
        deep_mode=deep_mode,
        images=images,
    )


def _placeholder_community_labels(communities) -> dict[int, str]:
    return {int(cid): f"Community {cid}" for cid in communities}

def _community_label_lines(G, communities, gods, max_communities, top_k):
    """One prompt line per community (largest first), sampling up to ``top_k``
    representative node labels (god nodes first). Returns (lines, labeled_cids);
    skips communities with no resolvable nodes."""
    # gods may be node-id strings or god_nodes() dicts ({"id": ..., "label": ...}).
    god_set = {g["id"] if isinstance(g, dict) else g for g in (gods or [])}
    ordered = sorted(communities.items(), key=lambda kv: -len(kv[1]))
    lines: list[str] = []
    labeled_cids: list[int] = []
    for cid, members in ordered[:max_communities]:
        ranked = [m for m in members if m in god_set] + [m for m in members if m not in god_set]
        names: list[str] = []
        seen: set[str] = set()
        for nid in ranked:
            label = str(G.nodes[nid].get("label", nid)) if nid in G.nodes else str(nid)
            label = label.strip().strip("()")[:_LABEL_MAXLEN]
            if label and label.lower() not in seen:
                seen.add(label.lower())
                names.append(label)
            if len(names) >= top_k:
                break
        if names:
            lines.append(f"Community {cid}: {', '.join(names)}")
            labeled_cids.append(int(cid))
    return lines, labeled_cids

def _parse_label_response(text: str, labeled_cids: list[int]) -> dict[int, str]:
    """Parse the backend's JSON ``{cid: name}`` reply. Raises on non-JSON or a
    non-object payload; silently ignores cids it didn't name."""
    cleaned = _LABEL_FENCE_RE.sub("", text.strip())
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            cleaned = cleaned[start:end + 1]
    data: dict | None = None
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            data = parsed
    except (json.JSONDecodeError, ValueError):
        data = None
    if data is None:
        # Salvage: pull the complete "<cid>": "<name>" pairs directly. A model
        # can truncate its reply mid-object (a stingy token budget or a preamble
        # eating the completion), which used to hard-fail the whole batch with
        # e.g. `Expecting value: line 1 column 6` on a `{"0":` fragment (#1690).
        # Recovering the pairs that DID arrive labels those communities instead
        # of dropping the entire batch to placeholders.
        pairs = re.findall(r'"?(-?\d+)"?\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', cleaned)
        if pairs:
            data = {k: v for k, v in pairs}
        else:
            raise ValueError(f"label response is not parseable JSON: {text[:120]!r}")
    out: dict[int, str] = {}
    for cid in labeled_cids:
        name = data.get(str(cid))
        if name is None:
            name = data.get(cid)
        if isinstance(name, str) and name.strip():
            out[cid] = name.strip()
    return out

def _label_batch_with_retry(
    batch_cids: list[int],
    batch_lines: list[str],
    *,
    backend: str,
    model: str | None,
    depth: int = 0,
    max_depth: int = 3,
    usage_out: dict | None = None,
) -> dict[int, str]:
    """Label a batch of communities, splitting in half and retrying on parse failure.

    Mirrors `_extract_with_adaptive_retry`'s recovery shape for the labeling path
    (#1278). When the LLM returns malformed JSON or a non-object payload, the
    batch is split at the midpoint and each half is retried recursively. Recursion
    is capped at ``max_depth`` to bound cost.

    Returns ``{cid: name}`` for everything that could be labeled. When a batch
    can't be split further (a single community, or ``depth >= max_depth``) and
    still won't parse, the parse error is **re-raised**: ``label_communities``
    catches it per batch and skips that batch (its communities stay unlabeled),
    re-raising only if every batch fails. Any non-parse exception (network,
    missing config, programming bug) propagates unchanged — those are never
    split-retried.
    """
    prompt = (
        "You are naming clusters in a knowledge graph. For each community below, "
        "return a concise 2-5 word plain-language name describing what it is about "
        "(e.g. \"Order Management\", \"Payment Flow\", \"Auth Middleware\"). "
        "Respond ONLY with a JSON object mapping the community id (as a string) to "
        "its name - no prose, no markdown fences.\n\n" + "\n".join(batch_lines)
    )
    # Budget generously: a 2-5 word name is ~10 tokens, but models (notably
    # gemini) often prepend a short preamble or reasoning that eats the
    # completion and truncates the JSON mid-object, which used to fail the whole
    # batch (#1690). The old 64 + 24*n floor left no headroom.
    max_tokens = _resolve_max_tokens(min(256 + 48 * len(batch_cids), 8192))
    call_kwargs: dict = {"backend": backend, "max_tokens": max_tokens}
    if model is not None:
        call_kwargs["model"] = model
    # Only forward usage_out when the caller wants accounting, so existing
    # callers (and their test doubles) see the unchanged _call_llm signature.
    if usage_out is not None:
        call_kwargs["usage_out"] = usage_out

    try:
        text = _call_llm(prompt, **call_kwargs)
        return _parse_label_response(text, batch_cids)
    except (json.JSONDecodeError, ValueError) as exc:
        # Parse failure. If we can still split, retry each half on a smaller
        # prompt (smaller output → less likely to truncate/mangle). At the base
        # case (single community or max depth) re-raise so the caller skips it.
        if len(batch_cids) <= 1 or depth >= max_depth:
            print(
                f"[purpory label] batch of {len(batch_cids)} still unparseable "
                f"at depth {depth} (cids={batch_cids[:5]}"
                f"{'...' if len(batch_cids) > 5 else ''}): {exc}",
                file=sys.stderr,
            )
            raise
        mid = len(batch_cids) // 2
        left = _label_batch_with_retry(
            batch_cids[:mid], batch_lines[:mid],
            backend=backend, model=model, depth=depth + 1, max_depth=max_depth,
            usage_out=usage_out,
        )
        right = _label_batch_with_retry(
            batch_cids[mid:], batch_lines[mid:],
            backend=backend, model=model, depth=depth + 1, max_depth=max_depth,
            usage_out=usage_out,
        )
        return left | right
