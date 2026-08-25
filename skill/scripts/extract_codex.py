#!/usr/bin/env python3
"""Replay a Codex JSONL session: user/agent text, summarized tools, subagent rollouts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

# Injected role=user blobs that are not the human's typed prompt.
_INJECTED_USER_PREFIXES = (
    "<recommended_plugins>",
    "<app-context>",
    "<skills_instructions>",
    "<permissions instructions>",
    "<collaboration_mode>",
    "<apps_instructions>",
    "<plugins_instructions>",
    "<codex_internal_context",
    "<INSTRUCTIONS>",
    "# AGENTS.md instructions",
    "<environment_context>",
    "<agent_transcripts>",
)

# Intra-agent / wait noise. Hidden unless ``--verbose-tools``.
_NOISY_TOOLS = frozenset(
    {
        "wait",
        "wait_agent",
        "list_agents",
        "send_message",
        "followup_task",
        "interrupt_agent",
    }
)

_SED_RANGE = re.compile(
    r"""sed\s+-n\s+(?:['"](?P<a>\d+),(?P<b>\d+)p['"]|(?P<a2>\d+),(?P<b2>\d+)p)\s+(?P<path>\S+)"""
)
_HEAD_N = re.compile(r"""head\s+(?:-n\s*)?(?P<n>\d+)\s+(?P<path>\S+)""")
_CAT = re.compile(r"""(?<![\w/.-])cat\s+(?P<path>\S+)""")
_PATCH_FILE = re.compile(
    r"\*\*\* (?P<op>Add|Update|Delete) File: (?P<path>.+?)(?:\\n|\n|$)"
)
_TOOLS_CALL = re.compile(r"tools\.([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_ONLY_CONNECTORS = re.compile(r"^(?:\s*(?:&&|;|then|fi|do|done|else|\|\||\|)+\s*)*$")


@dataclass
class SessionMeta:
    """Header fields taken from the first ``session_meta`` record."""

    session_id: str = ""
    cwd: str = ""
    branch: str = ""
    repo: str = ""
    started: str = ""
    originator: str = ""
    source_path: str = ""


@dataclass
class Event:
    """One replay line: a user prompt, agent message, or summarized tool act."""

    timestamp: str
    kind: str
    """``user``, ``agent``, or ``tool``."""

    text: str
    phase: str = ""
    """Agent phase: ``commentary`` or ``final_answer``."""

    tool: str = ""
    detail: str = ""
    """Extra one-line metadata (path, range, skill name)."""

    skip_verbose: bool = False
    """True for wait/subagent chatter hidden unless ``--verbose-tools``."""


@dataclass
class SubagentRef:
    """One spawned child, as recorded on the parent rollout."""

    thread_id: str
    """Child rollout UUID from ``sub_agent_activity.agent_thread_id``."""

    agent_path: str
    """Collaboration path such as ``/root/profile_004_head``."""

    short_name: str
    """Last path component; what ``--subagent`` usually matches."""

    first: str = ""
    last: str = ""
    kinds: Counter[str] = field(default_factory=Counter)
    log_path: Path | None = None
    """Child JSONL if a ``rollout-*-{thread_id}.jsonl`` exists."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the CLI parser."""

    parser = argparse.ArgumentParser(
        description=(
            "Replay a Codex ~/.codex/sessions JSONL log. Prints your prompts "
            "and the agent's output. File reads and skill loads keep the name "
            "and line range and drop the file/skill body."
        )
    )
    parser.add_argument(
        "path",
        help="JSONL path, or a session UUID (looked up under ~/.codex/)",
    )
    parser.add_argument(
        "--final-only",
        action="store_true",
        help="Drop agent commentary; keep only final_answer turns.",
    )
    parser.add_argument(
        "--no-tools",
        action="store_true",
        help="Print only user prompts and agent output (no tool lines).",
    )
    parser.add_argument(
        "--verbose-tools",
        action="store_true",
        help="Include wait/subagent tool calls that are hidden by default.",
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
            "List spawned children from this parent log (path, thread id, "
            "child JSONL) and exit."
        ),
    )
    parser.add_argument(
        "--subagent",
        metavar="NAME",
        help=(
            "Replay a child rollout instead of the parent. NAME is a short "
            "name (profile_004_head), /root/... path, or child thread UUID."
        ),
    )
    return parser.parse_args(argv)


def _codex_session_roots() -> tuple[Path, ...]:
    """Return Codex rollout directories that may contain JSONL logs."""

    return (
        Path.home() / ".codex" / "sessions",
        Path.home() / ".codex" / "archived_sessions",
    )


def find_rollout_by_uuid(uuid: str) -> Path | None:
    """Return the largest ``rollout-*-{uuid}.jsonl`` under Codex session dirs."""

    matches = [
        p
        for root in _codex_session_roots()
        if root.is_dir()
        for p in root.rglob(f"*{uuid}*.jsonl")
    ]
    if not matches:
        return None
    return max(matches, key=lambda p: p.stat().st_size)


def resolve_session_path(raw: str) -> Path:
    """Resolve a filesystem path or a session UUID to a JSONL file.

    If ``raw`` is a UUID, pick the largest matching rollout under
    ``~/.codex/sessions`` and ``~/.codex/archived_sessions``.
    """

    candidate = Path(raw).expanduser()
    if candidate.is_file():
        return candidate
    if candidate.exists():
        raise FileNotFoundError(f"not a JSONL file: {candidate}")

    uuid = raw.strip().lower()
    if not re.fullmatch(r"[0-9a-f-]{36}", uuid):
        raise FileNotFoundError(f"session log not found: {raw}")
    found = find_rollout_by_uuid(uuid)
    if found is None:
        raise FileNotFoundError(f"no JSONL for session {uuid}")
    return found


def _short_agent_name(agent_path: str) -> str:
    """Return the last component of a collaboration path."""

    stripped = (agent_path or "").rstrip("/")
    if "/" in stripped:
        return stripped.rsplit("/", 1)[-1]
    return stripped


def collect_subagents(path: Path) -> list[SubagentRef]:
    """Read ``sub_agent_activity`` events and attach each child's JSONL.

    The parent log does not contain the child's transcript. Each spawn
    writes a sibling ``rollout-*-{agent_thread_id}.jsonl``.
    """

    by_id: dict[str, SubagentRef] = {}
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = rec.get("payload") or {}
            if rec.get("type") != "event_msg":
                continue
            if payload.get("type") != "sub_agent_activity":
                continue
            thread_id = payload.get("agent_thread_id") or ""
            if not thread_id:
                continue
            agent_path = payload.get("agent_path") or ""
            stamp = rec.get("timestamp") or ""
            ref = by_id.get(thread_id)
            if ref is None:
                ref = SubagentRef(
                    thread_id=thread_id,
                    agent_path=agent_path,
                    short_name=_short_agent_name(agent_path) or thread_id,
                )
                by_id[thread_id] = ref
            elif agent_path and not ref.agent_path:
                ref.agent_path = agent_path
                ref.short_name = _short_agent_name(agent_path) or ref.short_name
            kind = payload.get("kind") or "unknown"
            ref.kinds[kind] += 1
            if not ref.first:
                ref.first = stamp
            ref.last = stamp

    refs = list(by_id.values())
    for ref in refs:
        ref.log_path = find_rollout_by_uuid(ref.thread_id)
    refs.sort(key=lambda r: r.first or r.thread_id)
    return refs


def _subagent_aliases(ref: SubagentRef) -> set[str]:
    """Return lowercase names that ``--subagent`` may use for ``ref``."""

    aliases = {
        ref.thread_id.lower(),
        ref.agent_path.lower(),
        ref.short_name.lower(),
    }
    if ref.agent_path.startswith("/"):
        aliases.add(ref.agent_path.lstrip("/").lower())
    return {a for a in aliases if a}


def resolve_subagent(refs: list[SubagentRef], name: str) -> SubagentRef:
    """Pick one child by short name, collaboration path, or thread UUID.

    Raises:
        ValueError: ``name`` matches none or more than one child.
    """

    query = name.strip().lower()
    if not query:
        raise ValueError("empty --subagent name")
    hits = [ref for ref in refs if query in _subagent_aliases(ref)]
    if len(hits) == 1:
        return hits[0]
    available = ", ".join(ref.short_name or ref.thread_id for ref in refs) or "(none)"
    if not hits:
        raise ValueError(f"no subagent matching {name!r}; available: {available}")
    raise ValueError(f"{name!r} matches {len(hits)} subagents; available: {available}")


def render_subagent_list(parent: Path, refs: list[SubagentRef]) -> str:
    """Render a one-block listing of spawned children."""

    lines = [f"subagents in {parent}  n={len(refs)}", ""]
    if not refs:
        lines.append("(none — this log has no sub_agent_activity events)")
        lines.append("")
        return "\n".join(lines)
    for ref in refs:
        kinds = " ".join(f"{k}={v}" for k, v in sorted(ref.kinds.items()))
        log = "MISSING"
        if ref.log_path is not None:
            size_mb = ref.log_path.stat().st_size / 1e6
            log = f"{ref.log_path}  {size_mb:.1f}MB"
        lines.append(ref.short_name)
        lines.append(f"  path      {ref.agent_path}")
        lines.append(f"  thread    {ref.thread_id}")
        if kinds:
            lines.append(f"  activity  {kinds}")
        if ref.first or ref.last:
            lines.append(f"  window    {_fmt_ts(ref.first)} -> {_fmt_ts(ref.last)}")
        lines.append(f"  log       {log}")
        lines.append("")
    return "\n".join(lines)


def _js_unescape(value: str) -> str:
    """Unescape a JS/JSON string body (``\\n``, ``\\'``, ``\\\"``)."""

    try:
        return bytes(value, "utf-8").decode("unicode_escape")
    except UnicodeDecodeError:
        return (
            value.replace(r"\n", "\n")
            .replace(r"\t", "\t")
            .replace(r"\'", "'")
            .replace(r"\"", '"')
            .replace(r"\\", "\\")
        )


def extract_js_string_field(blob: str, field: str) -> str | None:
    """Return the string value of ``field`` from a JS/JSON object literal."""

    patterns = (
        rf'"{field}"\s*:\s*"((?:\\.|[^"\\])*)"',
        rf"'{field}'\s*:\s*'((?:\\.|[^'\\])*)'",
        rf'{field}\s*:\s*"((?:\\.|[^"\\])*)"',
        rf"{field}\s*:\s*'((?:\\.|[^'\\])*)'",
    )
    for pat in patterns:
        match = re.search(pat, blob)
        if match:
            return _js_unescape(match.group(1))
    return None


def _extract_js_call_args(text: str, start: int) -> str:
    """Return the `{...}` argument blob of a ``tools.name(`` call at ``start``."""

    open_paren = text.find("(", start)
    if open_paren < 0:
        return ""
    i = open_paren + 1
    depth = 0
    in_str = None
    escape = False
    begin = None
    while i < len(text):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
        elif ch in ('"', "'"):
            in_str = ch
        elif ch == "{":
            if depth == 0:
                begin = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and begin is not None:
                return text[begin : i + 1]
        elif ch == ")" and depth == 0:
            break
        i += 1
    return ""


def iter_wrapped_tool_calls(exec_input: str) -> Iterator[tuple[str, str]]:
    """Yield ``(tool_name, argument_blob)`` from a Codex ``exec`` wrapper."""

    for match in _TOOLS_CALL.finditer(exec_input):
        name = match.group(1)
        blob = _extract_js_call_args(exec_input, match.start())
        yield name, blob


def _strip_quotes(token: str) -> str:
    """Strip one layer of matching quotes from a shell token."""

    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        return token[1:-1]
    return token.rstrip(";,")


def _looks_like_path(token: str) -> bool:
    """Return whether ``token`` is a plausible filesystem path."""

    if not token or token.startswith("-"):
        return False
    return "/" in token or token.endswith(
        (".md", ".py", ".cu", ".h", ".hpp", ".cc", ".cpp", ".json", ".txt", ".toml")
    )


def skill_name_from_path(path: str) -> str | None:
    """Return the skill folder name if ``path`` is a ``SKILL.md`` load."""

    normalized = path.replace("\\", "/")
    if not normalized.endswith("/SKILL.md") and not normalized.endswith("SKILL.md"):
        if "/SKILL.md" not in normalized:
            return None
    match = re.search(r"(?:\.agents/skills|/?skills)/([^/]+)/SKILL\.md$", normalized)
    if match:
        return match.group(1)
    return None


def _classify_sed_reads(cmd: str) -> list[tuple[str, str, str]]:
    """Extract ``(kind, path, range)`` from ``sed -n 'A,Bp' path`` clauses.

    ``kind`` is ``load-skill`` when the path is a skill ``SKILL.md``, else ``read``.
    """

    found: list[tuple[str, str, str]] = []
    for match in _SED_RANGE.finditer(cmd):
        start = match.group("a") or match.group("a2")
        end = match.group("b") or match.group("b2")
        path = _strip_quotes(match.group("path"))
        rng = f"L{start}-{end}"
        skill = skill_name_from_path(path)
        if skill:
            found.append(("load-skill", path, f"{skill} {rng}"))
        else:
            found.append(("read", path, rng))
    return found


def _is_pure_file_dump(cmd: str) -> bool:
    """Return whether ``cmd`` is only ``sed``/``head``/``cat`` file dumps."""

    leftover = _SED_RANGE.sub("", cmd)
    leftover = _HEAD_N.sub("", leftover)
    leftover = _CAT.sub("", leftover)
    leftover = leftover.replace("&&", " ").replace(";", " ").strip()
    return (not leftover) or bool(_ONLY_CONNECTORS.fullmatch(leftover))


def classify_shell_command(cmd: str) -> list[tuple[str, str, str]]:
    """Turn a shell ``cmd`` into compact ``(tool, primary, detail)`` rows.

    File-read and skill-load clauses become name+range rows and drop the
    file body. A command that is *only* those dumps is not also echoed.
    Mixed commands keep the original shell text as ``exec``.
    """

    rows = _classify_sed_reads(cmd)
    for match in _HEAD_N.finditer(cmd):
        path = _strip_quotes(match.group("path"))
        if _looks_like_path(path) and "$" not in path:
            rows.append(("read", path, f"L1-{match.group('n')}"))
    for match in _CAT.finditer(cmd):
        path = _strip_quotes(match.group("path"))
        if "$" in path:
            continue
        skill = skill_name_from_path(path)
        if skill:
            rows.append(("load-skill", path, skill))
        elif _looks_like_path(path):
            rows.append(("read", path, "full"))
    if _is_pure_file_dump(cmd) and rows:
        return rows
    if rows:
        leftover = _SED_RANGE.sub("", cmd)
        leftover = _HEAD_N.sub("", leftover)
        leftover = _CAT.sub("", leftover)
        leftover = re.sub(r"\s*(?:&&|;)+\s*", "; ", leftover).strip(" \t;&")
        if leftover and not _ONLY_CONNECTORS.fullmatch(leftover):
            rows.append(("exec", leftover, ""))
        return rows
    return [("exec", cmd, "")]


def summarize_apply_patch(blob: str) -> list[tuple[str, str, str]]:
    """Keep patched path + op; drop hunk bodies."""

    rows: list[tuple[str, str, str]] = []
    for match in _PATCH_FILE.finditer(blob):
        rows.append(("patch", match.group("path").strip(), match.group("op").lower()))
    return rows or [("patch", "(unparsed patch)", "")]


def summarize_update_plan(blob: str) -> list[tuple[str, str, str]]:
    """Keep plan step titles and statuses; drop long prose."""

    steps = re.findall(
        r'\{[^{}]*?"step"\s*:\s*"(.*?)"[^{}]*?"status"\s*:\s*"(.*?)"[^{}]*?\}',
        blob,
    )
    if not steps:
        return [("plan", extract_js_string_field(blob, "explanation") or "update_plan", "")]
    lines = [f"[{status}] {step}" for step, status in steps]
    return [("plan", "; ".join(lines), "")]


def summarize_exec_wrapper(exec_input: str) -> list[tuple[str, str, str]]:
    """Summarize a Codex ``custom_tool_call`` named ``exec``."""

    rows: list[tuple[str, str, str]] = []
    calls = list(iter_wrapped_tool_calls(exec_input))
    if not calls:
        # apply_patch often assigns ``const patch = "*** Begin Patch..."``.
        if "*** Begin Patch" in exec_input:
            return summarize_apply_patch(exec_input)
        return [("exec", exec_input.strip()[:240], "")]

    for name, blob in calls:
        if name == "exec_command":
            cmd = extract_js_string_field(blob, "cmd") or ""
            workdir = extract_js_string_field(blob, "workdir")
            classified = classify_shell_command(cmd)
            if workdir:
                classified = [
                    (kind, primary, detail or f"cwd={workdir}")
                    if kind == "exec" and not detail
                    else (kind, primary, detail)
                    for kind, primary, detail in classified
                ]
            rows.extend(classified)
        elif name == "apply_patch":
            rows.extend(summarize_apply_patch(exec_input))
        elif name == "update_plan":
            rows.extend(summarize_update_plan(blob or exec_input))
        elif name == "write_stdin":
            sid = extract_js_string_field(blob, "session_id") or "?"
            rows.append(("write_stdin", f"session {sid}", ""))
        elif name == "get_goal":
            rows.append(("get_goal", "", ""))
        else:
            rows.append((name, blob[:160].replace("\n", " ") if blob else "", ""))
    return rows


def _payload_text(content: Any) -> str:
    """Join text parts from a response_item ``content`` list or string."""

    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            text = (
                item.get("text")
                or item.get("input_text")
                or item.get("output_text")
                or ""
            )
            if text:
                parts.append(text)
        elif isinstance(item, str):
            parts.append(item)
    return "\n".join(parts)


def is_injected_user_text(text: str) -> bool:
    """Return whether a role=user blob is harness injection, not a prompt."""

    stripped = text.lstrip()
    return any(stripped.startswith(prefix) for prefix in _INJECTED_USER_PREFIXES)


def _parse_ts(stamp: str) -> datetime:
    """Parse a Codex ISO timestamp; fall back to epoch on failure."""

    if not stamp:
        return datetime.min
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min


def _fmt_ts(stamp: str) -> str:
    """Format a timestamp as ``MM-DD HH:MM:SS`` in local time."""

    parsed = _parse_ts(stamp)
    if parsed == datetime.min:
        return stamp
    return parsed.astimezone().strftime("%m-%d %H:%M:%S")


def _json_args(raw: Any) -> dict[str, Any]:
    """Parse a function_call ``arguments`` field that may be a JSON string."""

    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(value, dict):
            return value
    return {}


def parse_session(path: Path) -> tuple[SessionMeta, list[Event]]:
    """Walk a rollout JSONL and collect replay events in file order.

    User prompts and agent output come from ``event_msg.user_message`` /
    ``event_msg.agent_message`` on older logs, and from
    ``response_item.message`` (``role=user|assistant``) on current Codex
    Desktop logs. The two sources overlap on older logs; text is
    de-duplicated. Tool calls come from ``response_item`` and are
    summarized; read/skill bodies are dropped.
    """

    meta = SessionMeta(source_path=str(path))
    events: list[Event] = []
    seen_user: set[str] = set()
    seen_agent: set[tuple[str, str]] = set()

    def add_user(stamp: str, text: str) -> None:
        text = text.strip()
        if not text or is_injected_user_text(text) or text in seen_user:
            return
        seen_user.add(text)
        events.append(Event(timestamp=stamp, kind="user", text=text))

    def add_agent(stamp: str, text: str, phase: str) -> None:
        text = text.strip()
        if not text:
            return
        key = (phase, text)
        if key in seen_agent:
            return
        seen_agent.add(key)
        events.append(Event(timestamp=stamp, kind="agent", text=text, phase=phase))

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
            payload = rec.get("payload") or {}
            stamp = rec.get("timestamp") or ""

            if rec_type == "session_meta" and not meta.session_id:
                meta.session_id = payload.get("session_id") or payload.get("id") or ""
                meta.cwd = payload.get("cwd") or ""
                meta.started = payload.get("timestamp") or stamp
                meta.originator = payload.get("originator") or ""
                git = payload.get("git") or {}
                meta.branch = git.get("branch") or ""
                meta.repo = git.get("repository_url") or ""
                continue

            if rec_type == "event_msg":
                ev = payload.get("type")
                if ev == "user_message":
                    add_user(stamp, payload.get("message") or "")
                elif ev == "agent_message":
                    add_agent(stamp, payload.get("message") or "", payload.get("phase") or "")
                continue

            if rec_type != "response_item":
                continue

            item_type = payload.get("type")
            if item_type == "message":
                role = payload.get("role")
                text = _payload_text(payload.get("content"))
                if role == "user":
                    add_user(stamp, text)
                elif role == "assistant":
                    add_agent(stamp, text, payload.get("phase") or "")
                continue

            if item_type == "custom_tool_call" and payload.get("name") == "exec":
                for tool, primary, detail in summarize_exec_wrapper(
                    payload.get("input") or ""
                ):
                    events.append(
                        Event(
                            timestamp=stamp,
                            kind="tool",
                            text=primary,
                            tool=tool,
                            detail=detail,
                        )
                    )
                continue

            if item_type == "function_call":
                name = payload.get("name") or "call"
                args = _json_args(payload.get("arguments"))
                skip = name in _NOISY_TOOLS
                if name == "spawn_agent":
                    primary = args.get("task_name") or ""
                    detail = f"fork={args.get('fork_turns', '')}"
                elif name == "interrupt_agent":
                    primary = str(args.get("target") or "")
                    detail = ""
                elif name in {"send_message", "followup_task"}:
                    primary = str(args.get("target") or "")
                    detail = name
                elif name == "wait_agent":
                    primary = f"timeout_ms={args.get('timeout_ms', '')}"
                    detail = ""
                elif name == "request_user_input":
                    primary = str(args.get("question") or args.get("prompt") or "")
                    detail = ""
                    skip = False
                else:
                    # Keep the name; drop encrypted / bulky argument bodies.
                    keep_keys = [
                        k
                        for k in args
                        if k not in {"message", "encrypted_content", "payload"}
                    ]
                    primary = ", ".join(f"{k}={args[k]}" for k in keep_keys[:6])
                    detail = ""
                events.append(
                    Event(
                        timestamp=stamp,
                        kind="tool",
                        text=primary,
                        tool=name,
                        detail=detail,
                        skip_verbose=skip,
                    )
                )

    return meta, events


def render_text(
    meta: SessionMeta,
    events: Iterable[Event],
    *,
    markdown: bool,
    product: str = "Codex",
) -> str:
    """Render a chronological transcript."""

    lines: list[str] = []
    if markdown:
        lines.append(
            f"# {product} session `{meta.session_id or Path(meta.source_path).name}`"
        )
        lines.append("")
        for label, value in (
            ("file", meta.source_path),
            ("cwd", meta.cwd),
            ("branch", meta.branch),
            ("started", meta.started),
            ("originator", meta.originator),
        ):
            if value:
                lines.append(f"- **{label}:** `{value}`")
        lines.append("")
    else:
        lines.append("=" * 72)
        lines.append(f"session  {meta.session_id}")
        if meta.source_path:
            lines.append(f"file     {meta.source_path}")
        if meta.cwd:
            lines.append(f"cwd      {meta.cwd}")
        if meta.branch:
            lines.append(f"branch   {meta.branch}")
        if meta.started:
            lines.append(f"started  {meta.started}")
        lines.append("=" * 72)
        lines.append("")

    for event in events:
        when = _fmt_ts(event.timestamp)
        if event.kind == "user":
            if markdown:
                lines.append(f"## YOU · {when}")
                lines.append("")
                lines.append(event.text)
                lines.append("")
            else:
                lines.append(f"----- YOU  {when} -----")
                lines.append(event.text)
                lines.append("")
        elif event.kind == "agent":
            phase = event.phase or "output"
            if markdown:
                lines.append(f"## AGENT · {phase} · {when}")
                lines.append("")
                lines.append(event.text)
                lines.append("")
            else:
                lines.append(f"----- AGENT [{phase}]  {when} -----")
                lines.append(event.text)
                lines.append("")
        elif event.kind == "tool":
            detail = f"  {event.detail}" if event.detail else ""
            body = event.text.replace("\n", " ")
            if markdown:
                lines.append(f"- `{event.tool}` {body}{detail}".rstrip())
            else:
                lines.append(f"  [{event.tool}] {body}{detail}".rstrip())
    if events and events[-1].kind == "tool" and markdown:
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def filter_events(
    events: list[Event],
    *,
    final_only: bool,
    no_tools: bool,
    verbose_tools: bool,
) -> list[Event]:
    """Apply CLI visibility flags."""

    out: list[Event] = []
    for event in events:
        if event.kind == "agent" and final_only and event.phase == "commentary":
            continue
        if event.kind == "tool":
            if no_tools:
                continue
            if event.skip_verbose and not verbose_tools:
                continue
        out.append(event)
    return out


def main(argv: list[str] | None = None) -> int:
    """Parse one session log and print the replay to stdout."""

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
                "but no child JSONL was found under ~/.codex/",
                file=sys.stderr,
            )
            return 2
        path = ref.log_path

    meta, events = parse_session(path)
    events = filter_events(
        events,
        final_only=args.final_only,
        no_tools=args.no_tools,
        verbose_tools=args.verbose_tools,
    )
    sys.stdout.write(
        render_text(meta, events, markdown=args.format == "md")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
