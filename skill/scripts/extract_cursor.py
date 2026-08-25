#!/usr/bin/env python3
"""Replay a Cursor agent-transcript JSONL: user/agent text, summarized tools."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from extract_codex import (
    Event,
    SessionMeta,
    SubagentRef,
    classify_shell_command,
    filter_events,
    render_subagent_list,
    render_text,
    skill_name_from_path,
)

# Cursor wraps the typed prompt. The inner ``<user_query>`` is the human.
_USER_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)
_TIMESTAMP = re.compile(r"<timestamp>\s*(.*?)\s*</timestamp>", re.S)
_SKILL_NAME = re.compile(r"^Skill Name:\s*(.+)\s*$", re.M)
_WRAPPER_BLOCK = re.compile(
    r"<(manually_attached_skills|timestamp|agent_transcripts|"
    r"open_and_recently_viewed_files|user_info|agent_skills|"
    r"attached_files|image_files|system_reminder)(?:\s[^>]*)?>.*?</\1>",
    re.S,
)
_UUID = re.compile(
    r"(?:agent-)?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.I,
)

# Auto-injected follow-ups, not a typed prompt.
_INJECTED_QUERY_PREFIXES = (
    "Briefly inform the user about the task result",
)

# Tool chatter hidden unless ``--verbose-tools``.
_NOISY_TOOLS = frozenset(
    {
        "UpdateCurrentStep",
        "AwaitShell",
        "GetDynamicTools",
    }
)

_CURSOR_TS_FORMATS = (
    "%A, %b %d, %Y, %I:%M %p",
    "%A, %B %d, %Y, %I:%M %p",
    "%a, %b %d, %Y, %I:%M %p",
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Replay a Cursor ~/.cursor/projects/*/agent-transcripts JSONL. "
            "Prints your prompts and the agent's output. File reads and "
            "edits keep the name (and line range) and drop the file body."
        )
    )
    parser.add_argument(
        "path",
        help="JSONL path, transcript directory, or a conversation UUID",
    )
    parser.add_argument(
        "--no-tools",
        action="store_true",
        help="Print only user prompts and agent output (no tool lines).",
    )
    parser.add_argument(
        "--verbose-tools",
        action="store_true",
        help="Include wait/discovery tool calls that are hidden by default.",
    )
    parser.add_argument(
        "--format",
        choices=("text", "md"),
        default="text",
        help="text (default) or markdown.",
    )
    parser.add_argument(
        "--list-subagents",
        action="store_true",
        help=(
            "List child transcripts under this parent (path, thread id) "
            "and exit."
        ),
    )
    parser.add_argument(
        "--subagent",
        metavar="NAME",
        help=(
            "Replay a child transcript instead of the parent. NAME is a "
            "short Task description or the child UUID."
        ),
    )
    return parser.parse_args(argv)


def _cursor_projects_root() -> Path:
    """Return ``~/.cursor/projects``."""

    return Path.home() / ".cursor" / "projects"


def find_transcript_by_uuid(uuid: str) -> Path | None:
    """Return the best ``*.jsonl`` matching ``uuid`` under Cursor projects.

    Prefers a parent ``{uuid}/{uuid}.jsonl`` over a ``subagents/`` child
    of the same id. Among remaining hits, pick the largest file.
    """

    needle = uuid.strip().lower().removeprefix("agent-")
    root = _cursor_projects_root()
    if not root.is_dir():
        return None

    matches = [
        p
        for p in root.rglob("*.jsonl")
        if "agent-transcripts" in p.parts and needle in p.name.lower()
    ]
    if not matches:
        return None

    def rank(path: Path) -> tuple[int, int]:
        in_sub = 1 if "subagents" in path.parts else 0
        return (in_sub, -path.stat().st_size)

    return min(matches, key=rank)


def resolve_session_path(raw: str) -> Path:
    """Resolve a filesystem path or a conversation UUID to a JSONL file."""

    candidate = Path(raw).expanduser()
    if candidate.is_file():
        return candidate
    if candidate.is_dir():
        named = candidate / f"{candidate.name}.jsonl"
        if named.is_file():
            return named
        jsonls = sorted(candidate.glob("*.jsonl"))
        if len(jsonls) == 1:
            return jsonls[0]
        raise FileNotFoundError(f"not a JSONL file: {candidate}")
    if candidate.exists():
        raise FileNotFoundError(f"not a JSONL file: {candidate}")

    match = _UUID.fullmatch(raw.strip()) or _UUID.search(raw.strip())
    if not match:
        raise FileNotFoundError(f"session log not found: {raw}")
    found = find_transcript_by_uuid(match.group(1))
    if found is None:
        raise FileNotFoundError(f"no JSONL for conversation {match.group(1)}")
    return found


def _project_slug(path: Path) -> str:
    """Return the ``~/.cursor/projects/<slug>`` directory name, if any."""

    parts = path.parts
    if "projects" not in parts:
        return ""
    idx = parts.index("projects")
    if idx + 1 < len(parts):
        return parts[idx + 1]
    return ""


def _session_id_from_path(path: Path) -> str:
    """Return the conversation UUID encoded in ``path``."""

    match = _UUID.search(path.name) or _UUID.search(str(path))
    return match.group(1).lower() if match else path.stem


def cursor_timestamp_to_iso(raw: str) -> str:
    """Parse a Cursor ``<timestamp>`` value to an ISO-8601 string."""

    body = re.sub(r"\s*\([^)]*\)\s*$", "", (raw or "").strip())
    if not body:
        return ""
    for fmt in _CURSOR_TS_FORMATS:
        try:
            return datetime.strptime(body, fmt).isoformat()
        except ValueError:
            continue
    return raw.strip()


def extract_user_prompt(text: str) -> tuple[str, str, list[str]]:
    """Split a Cursor user blob into prompt, timestamp, attached skills.

    Prefers the inner ``<user_query>``. Wrapper blocks (attached skills,
    timestamps, IDE chrome) are dropped from the prompt.
    """

    stamp = ""
    match = _TIMESTAMP.search(text)
    if match:
        stamp = cursor_timestamp_to_iso(match.group(1))
    skills = [name.strip() for name in _SKILL_NAME.findall(text) if name.strip()]
    queries = [q.strip() for q in _USER_QUERY.findall(text) if q.strip()]
    if queries:
        prompt = "\n\n".join(queries)
    else:
        prompt = _WRAPPER_BLOCK.sub("", text).strip()
    return prompt, stamp, skills


def is_injected_user_prompt(text: str) -> bool:
    """Return whether ``text`` is a harness follow-up, not a typed prompt."""

    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in _INJECTED_QUERY_PREFIXES)


def _read_range(inp: dict[str, Any]) -> str:
    """Return a compact ``Lstart-end`` (or ``full``) for a Read call."""

    offset = inp.get("offset")
    limit = inp.get("limit")
    try:
        offset_n = int(offset) if offset is not None else None
    except (TypeError, ValueError):
        offset_n = None
    try:
        limit_n = int(limit) if limit is not None else None
    except (TypeError, ValueError):
        limit_n = None
    if offset_n is not None and limit_n is not None:
        return f"L{offset_n}-{offset_n + limit_n - 1}"
    if offset_n is not None:
        return f"L{offset_n}+"
    if limit_n is not None:
        return f"L1-{limit_n}"
    return "full"


def _join_detail(*parts: Any) -> str:
    """Join non-empty detail fragments with `` · ``."""

    return " · ".join(str(p) for p in parts if p not in (None, "", [], ()))


def summarize_cursor_tool(name: str, inp: Any) -> list[tuple[str, str, str, bool]]:
    """Turn one ``tool_use`` into compact ``(tool, primary, detail, skip)`` rows.

    File reads/edits keep the path and drop bodies. A shell that is only
    ``sed``/``head``/``cat`` dumps becomes ``read`` / ``load-skill`` rows.
    """

    args = inp if isinstance(inp, dict) else {}
    skip = name in _NOISY_TOOLS

    if name == "Read":
        path = str(args.get("path") or "")
        skill = skill_name_from_path(path)
        rng = _read_range(args)
        if skill:
            return [("load-skill", path, _join_detail(skill, rng), False)]
        return [("read", path, rng, False)]

    if name == "Write":
        return [("write", str(args.get("path") or ""), "", False)]

    if name == "StrReplace":
        return [("patch", str(args.get("path") or ""), "update", False)]

    if name == "Delete":
        return [("delete", str(args.get("path") or ""), "", False)]

    if name == "EditNotebook":
        return [
            (
                "edit-notebook",
                str(args.get("target_notebook") or ""),
                _join_detail(args.get("cell_language"), args.get("cell_idx")),
                False,
            )
        ]

    if name == "Shell":
        cmd = str(args.get("command") or "")
        workdir = args.get("working_directory")
        rows = classify_shell_command(cmd)
        out: list[tuple[str, str, str, bool]] = []
        for kind, primary, detail in rows:
            if kind == "exec" and not detail and workdir:
                detail = f"cwd={workdir}"
            out.append((kind, primary, detail, False))
        return out

    if name == "Grep":
        return [
            (
                "grep",
                str(args.get("pattern") or ""),
                _join_detail(args.get("path"), args.get("glob"), args.get("output_mode")),
                False,
            )
        ]

    if name == "Glob":
        return [
            (
                "glob",
                str(args.get("glob_pattern") or ""),
                str(args.get("target_directory") or ""),
                False,
            )
        ]

    if name == "Task":
        primary = str(args.get("description") or args.get("subagent_type") or "task")
        detail = _join_detail(args.get("subagent_type"), args.get("resume"))
        return [("spawn_agent", primary, detail, False)]

    if name == "TodoWrite":
        todos = args.get("todos") or []
        bits: list[str] = []
        if isinstance(todos, list):
            for item in todos:
                if not isinstance(item, dict):
                    continue
                status = item.get("status") or ""
                content = str(item.get("content") or item.get("id") or "")
                bits.append(f"[{status}] {content}" if status else content)
        return [("plan", "; ".join(bits) or "TodoWrite", "", False)]

    if name == "CreatePlan":
        return [("plan", str(args.get("name") or "CreatePlan"), "", False)]

    if name == "GetDynamicTools":
        return [
            (
                "tools",
                str(args.get("namespace") or args.get("toolName") or ""),
                str(args.get("toolName") or ""),
                True,
            )
        ]

    if name == "CallDynamicTool":
        ns = args.get("namespace") or ""
        tool = args.get("toolName") or ""
        return [("call", f"{ns}.{tool}".strip("."), "", False)]

    if name == "WebSearch":
        return [("web-search", str(args.get("search_term") or ""), "", False)]

    if name == "WebFetch":
        return [("web-fetch", str(args.get("url") or ""), "", False)]

    if name == "AwaitShell":
        return [("wait", str(args.get("shell_id") or ""), "", True)]

    if name == "AskQuestion":
        return [("ask", str(args.get("title") or args.get("prompt") or ""), "", False)]

    if name == "ReadLints":
        paths = args.get("paths") or []
        if isinstance(paths, list):
            primary = ", ".join(str(p) for p in paths[:4])
        else:
            primary = str(paths)
        return [("read-lints", primary, "", False)]

    if name == "SwitchMode":
        return [("switch-mode", str(args.get("target_mode_id") or ""), "", False)]

    if name == "UpdateCurrentStep":
        return [("step", str(args.get("current_step") or ""), "", True)]

    keep = [
        f"{k}={args[k]}"
        for k in args
        if k
        not in {
            "contents",
            "old_string",
            "new_string",
            "prompt",
            "plan",
            "command",
        }
    ]
    return [(name, ", ".join(keep[:6]), "", skip)]


def _content_items(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the message content list from a Cursor JSONL record."""

    message = rec.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            return [c for c in content if isinstance(c, dict)]
    content = rec.get("content")
    if isinstance(content, list):
        return [c for c in content if isinstance(c, dict)]
    return []


def parse_session(path: Path) -> tuple[SessionMeta, list[Event]]:
    """Walk a Cursor transcript JSONL and collect replay events.

    User prompts are the inner ``<user_query>``. Attached skills become
    ``load-skill`` rows. Assistant ``text`` is agent output; ``tool_use``
    is summarized and file bodies are dropped.
    """

    slug = _project_slug(path)
    meta = SessionMeta(
        source_path=str(path),
        session_id=_session_id_from_path(path),
        cwd=slug,
        repo=slug,
    )
    events: list[Event] = []

    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            rec_type = rec.get("type")
            if rec_type == "turn_ended":
                continue

            role = rec.get("role")
            items = _content_items(rec)
            if role == "user":
                texts = [
                    item.get("text") or ""
                    for item in items
                    if item.get("type") == "text"
                ]
                blob = "\n".join(t for t in texts if t)
                prompt, stamp, skills = extract_user_prompt(blob)
                if stamp and not meta.started:
                    meta.started = stamp
                for skill in skills:
                    events.append(
                        Event(
                            timestamp=stamp,
                            kind="tool",
                            text=skill,
                            tool="load-skill",
                            detail=skill,
                        )
                    )
                if prompt and not is_injected_user_prompt(prompt):
                    events.append(Event(timestamp=stamp, kind="user", text=prompt))
                continue

            if role != "assistant":
                continue

            for item in items:
                kind = item.get("type")
                if kind == "text":
                    text = (item.get("text") or "").strip()
                    if text:
                        events.append(
                            Event(timestamp="", kind="agent", text=text, phase="output")
                        )
                elif kind == "tool_use":
                    name = item.get("name") or "call"
                    for tool, primary, detail, skip in summarize_cursor_tool(
                        name, item.get("input")
                    ):
                        events.append(
                            Event(
                                timestamp="",
                                kind="tool",
                                text=primary,
                                tool=tool,
                                detail=detail,
                                skip_verbose=skip,
                            )
                        )

    return meta, events


def _subagents_dir(parent: Path) -> Path:
    """Return the ``subagents/`` folder next to a parent transcript."""

    if parent.parent.name == "subagents":
        return parent.parent
    return parent.parent / "subagents"


def collect_subagents(path: Path) -> list[SubagentRef]:
    """List child JSONLs under ``<parent>/subagents/``.

    Task ``description`` values from the parent are attached when the
    child's first user prompt matches that Task's ``prompt``.
    """

    child_dir = _subagents_dir(path)
    if path.parent.name == "subagents":
        return []
    if not child_dir.is_dir():
        return []

    task_labels: list[tuple[str, str]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            for item in _content_items(rec):
                if item.get("type") != "tool_use" or item.get("name") != "Task":
                    continue
                inp = item.get("input") or {}
                if not isinstance(inp, dict):
                    continue
                desc = str(inp.get("description") or "").strip()
                prompt = str(inp.get("prompt") or "").strip()
                if desc or prompt:
                    task_labels.append((desc, prompt))

    refs: list[SubagentRef] = []
    for child in sorted(child_dir.glob("*.jsonl")):
        thread_id = _session_id_from_path(child)
        short = child.stem
        first_prompt = ""
        try:
            _, child_events = parse_session(child)
        except OSError:
            child_events = []
        for event in child_events:
            if event.kind == "user":
                first_prompt = event.text
                break
        for desc, prompt in task_labels:
            hay = first_prompt[:400]
            if prompt and (prompt[:200] in first_prompt or hay in prompt):
                short = desc or short
                break
        kinds: Counter[str] = Counter()
        kinds["child"] = 1
        refs.append(
            SubagentRef(
                thread_id=thread_id,
                agent_path=short,
                short_name=short,
                first=next((e.timestamp for e in child_events if e.timestamp), ""),
                log_path=child,
                kinds=kinds,
            )
        )
    return refs


def resolve_subagent(refs: list[SubagentRef], name: str) -> SubagentRef:
    """Pick one child by UUID or Task description.

    Raises:
        ValueError: ``name`` matches none or more than one child.
    """

    query = name.strip().lower()
    if not query:
        raise ValueError("empty --subagent name")
    hits = [
        ref
        for ref in refs
        if query in {
            ref.thread_id.lower(),
            ref.short_name.lower(),
            ref.agent_path.lower(),
            (ref.log_path.stem.lower() if ref.log_path is not None else ""),
        }
        or query in ref.short_name.lower()
        or query in ref.thread_id.lower()
    ]
    if len(hits) == 1:
        return hits[0]
    available = ", ".join(ref.short_name or ref.thread_id for ref in refs) or "(none)"
    if not hits:
        raise ValueError(f"no subagent matching {name!r}; available: {available}")
    raise ValueError(f"{name!r} matches {len(hits)} subagents; available: {available}")


def main(argv: list[str] | None = None) -> int:
    """Parse one Cursor transcript and print the replay to stdout."""

    args = parse_args(argv)
    try:
        path = resolve_session_path(args.path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list_subagents or args.subagent:
        refs = collect_subagents(path)
        if args.list_subagents:
            sys.stdout.write(render_subagent_list(path, refs))
            return 0
        try:
            ref = resolve_subagent(refs, args.subagent)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if ref.log_path is None:
            print(
                f"error: subagent {ref.short_name} has thread {ref.thread_id} "
                "but no child JSONL was found",
                file=sys.stderr,
            )
            return 2
        path = ref.log_path

    meta, events = parse_session(path)
    events = filter_events(
        events,
        final_only=False,
        no_tools=args.no_tools,
        verbose_tools=args.verbose_tools,
    )
    sys.stdout.write(
        render_text(meta, events, markdown=args.format == "md", product="Cursor")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
