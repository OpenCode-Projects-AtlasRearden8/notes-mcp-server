#!/usr/bin/env python3
"""
notes-vault-mcp: a small Model Context Protocol (MCP) server that gives an
MCP client safe access to one folder of Markdown notes.

The server speaks the MCP JSON-RPC 2.0 protocol over stdio, using
newline-delimited JSON as the transport framing (per the MCP stdio
transport spec). It uses only the Python standard library.

Every request is sandboxed to the vault folder:

  * only relative paths, no '..' segments, no absolute/Windows paths
  * only .md / .markdown files, never hidden items (paths starting with '.')
  * symlinks that would resolve outside the vault are blocked
  * optional allowlist restricting tools to specific subfolders
  * every write to an existing note saves a backup under .backups/ first

Usage:
    python3 notes_mcp_server.py [--vault PATH] [--allow-folder FOLDER] ...

Environment:
    NOTES_VAULT              default vault folder
    NOTES_ALLOWED_FOLDERS    comma-separated subfolders to allowlist

If neither --vault nor NOTES_VAULT is given, the shipped ./sample_vault is
used so the server works out of the box.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import shutil
import sys
from datetime import datetime, timezone

SERVER_NAME = "notes-vault-mcp"
SERVER_VERSION = "1.0.0"
SUPPORTED_PROTOCOL_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[-1]

NOTE_EXTENSIONS = (".md", ".markdown")
BACKUP_DIRNAME = ".backups"
HIDDEN_PREFIX = "."
MAX_READ_BYTES = 1_000_000
MAX_SEARCH_FILE_BYTES = 2_000_000
DEFAULT_LIST_LIMIT = 200
MAX_LIST_LIMIT = 500
DEFAULT_SEARCH_LIMIT = 50
MAX_SEARCH_LIMIT = 200

LOG_PREFIX = "[notes-vault-mcp]"

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_VAULT = os.path.join(REPO_DIR, "sample_vault")


class NotesError(Exception):
    """A friendly, user-facing rule violation inside a tool call."""


class ValidationError(NotesError):
    """Tool arguments failed their schema checks (reported as MCP -32602)."""


def log(*parts):
    print(LOG_PREFIX, *parts, file=sys.stderr, flush=True)


def _is_absolute_path(text):
    drive, _ = posixpath.splitdrive(text)
    return bool(drive) or text.startswith("/")


def _split_relative(text, kind, allow_empty=False):
    """Validate a relative path and return its clean forward-slash parts."""
    if not isinstance(text, str):
        raise NotesError(f"The {kind} path must be text.")
    cleaned = text.strip()
    if not cleaned:
        if allow_empty:
            return []
        raise NotesError(f"The {kind} path cannot be empty.")
    if "\x00" in cleaned:
        raise NotesError(f"The {kind} path contains an invalid character (NUL).")
    cleaned = cleaned.replace("\\", "/")
    if _is_absolute_path(cleaned):
        raise NotesError(
            f"'{text}' looks like an absolute path. Use a path relative to the "
            f"vault instead (for example 'todo.md' or 'ideas/chess.md')."
        )
    parts = []
    for part in cleaned.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise NotesError(
                "'..' is not allowed in paths: moving outside the vault is "
                "blocked for your safety."
            )
        if part.startswith(HIDDEN_PREFIX):
            raise NotesError(
                "Hidden items (paths starting with '.') are not accessible "
                "through this server, including the .backups folder."
            )
        if ":" in part:
            raise NotesError(
                "':' is not allowed in paths (colons look like drive letters)."
            )
        parts.append(part)
    if not parts:
        if allow_empty:
            return []
        raise NotesError(f"The {kind} path cannot be empty.")
    return parts


def normalize_note_path(text):
    parts = _split_relative(text, "note")
    rel = "/".join(parts)
    extension = posixpath.splitext(rel)[1].lower()
    if extension not in NOTE_EXTENSIONS:
        raise NotesError(
            f"Only Markdown notes ({', '.join(NOTE_EXTENSIONS)}) can be "
            f"accessed, not '{rel}'."
        )
    return rel


def normalize_folder_path(text):
    parts = _split_relative(text, "folder", allow_empty=True)
    return "/".join(parts)


def _iso_utc(timestamp):
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(
        timespec="seconds"
    )


class Vault:
    """The on-disk notes folder plus every safety rule for touching it."""

    def __init__(self, root, allowed_folders=None):
        if root is None or not str(root).strip():
            raise NotesError("The vault path is empty.")
        path = os.path.abspath(os.path.expanduser(str(root)))
        if not os.path.isdir(path):
            raise NotesError(
                f"Vault folder does not exist: {path} "
                "(create it first, e.g. mkdir /path/to/notes, or pass "
                "--vault ./sample_vault)"
            )
        self.root = os.path.realpath(path)
        if allowed_folders is None:
            allowed_folders = [""]
        cleaned = []
        for entry in allowed_folders:
            normalized = normalize_folder_path(entry)
            if normalized not in cleaned:
                cleaned.append(normalized)
        self.allowed_folders = cleaned

    def folder_allowed(self, rel_folder):
        for entry in self.allowed_folders:
            if entry == "" or rel_folder == entry or rel_folder.startswith(entry + "/"):
                return True
        return False

    def require_folder_allowed(self, rel_folder):
        if not self.folder_allowed(rel_folder):
            shown = ", ".join(f"'{e}'" for e in self.allowed_folders)
            raise NotesError(
                f"Folder '{rel_folder or '(vault root)'}' is not in the "
                f"allowlist (allowed: {shown}). Edit is limited to those "
                "folders by configuration."
            )

    def _under_root(self, rel_path):
        target = os.path.join(self.root, *rel_path.split("/"))
        real = os.path.realpath(target)
        if not (real == self.root or real.startswith(self.root + os.sep)):
            raise NotesError(
                f"'{rel_path}' resolves outside the vault through a link; "
                "blocked for safety."
            )
        return real

    def note_file(self, text):
        rel = normalize_note_path(text)
        self.require_folder_allowed(posixpath.dirname(rel))
        return rel, self._under_root(rel)

    def folder_base(self, text):
        rel = normalize_folder_path(text)
        self.require_folder_allowed(rel)
        if rel == "":
            return self.root
        real = self._under_root(rel)
        if not os.path.isdir(real):
            raise NotesError(
                f"Folder '{rel}' does not exist in the vault yet "
                "(the MCP tools cannot create folders on their own)."
            )
        return real

    def note_files(self, folder_text=""):
        """Yield (rel_path, absolute_path) for markdown files in `folder_text`."""
        base = self.folder_base(folder_text)
        for dirpath, dirnames, filenames in os.walk(base):
            rel_dir = os.path.relpath(dirpath, self.root).replace(os.sep, "/")
            if rel_dir == ".":
                rel_dir = ""
            keep = []
            for name in sorted(dirnames):
                if name.startswith(HIDDEN_PREFIX):
                    continue
                child_rel = f"{rel_dir}/{name}" if rel_dir else name
                if self.folder_allowed(child_rel):
                    keep.append(name)
            dirnames[:] = keep
            for name in sorted(filenames):
                if name.startswith(HIDDEN_PREFIX):
                    continue
                if posixpath.splitext(name)[1].lower() not in NOTE_EXTENSIONS:
                    continue
                rel_file = f"{rel_dir}/{name}" if rel_dir else name
                yield rel_file, os.path.join(dirpath, name)

    def list_notes(self, folder="", limit=DEFAULT_LIST_LIMIT):
        if not isinstance(limit, int) or isinstance(limit, bool) or not (
            1 <= limit <= MAX_LIST_LIMIT
        ):
            raise ValidationError(
                f"'limit' must be a whole number from 1 to {MAX_LIST_LIMIT}."
            )
        notes = []
        for rel, real in self.note_files(folder):
            notes.append(
                {
                    "path": rel,
                    "size_bytes": os.path.getsize(real),
                    "modified_utc": _iso_utc(os.path.getmtime(real)),
                }
            )
            if len(notes) >= limit:
                break
        return {
            "folder": normalize_folder_path(folder),
            "count": len(notes),
            "complete": len(notes) < limit,
            "notes": notes,
        }

    def read_note(self, path):
        rel, real = self.note_file(path)
        if os.path.isdir(real):
            raise NotesError(f"'{rel}' is a folder, not a note.")
        if not os.path.isfile(real):
            raise NotesError(f"Note not found: {rel}")
        size = os.path.getsize(real)
        truncated = size > MAX_READ_BYTES
        with open(real, "r", encoding="utf-8", errors="replace") as handle:
            content = handle.read(MAX_READ_BYTES)
        return {
            "path": rel,
            "content": content,
            "size_bytes": size,
            "modified_utc": _iso_utc(os.path.getmtime(real)),
            "truncated": truncated,
        }

    def search_notes(self, query, folder="", case_sensitive=False, limit=DEFAULT_SEARCH_LIMIT):
        if not isinstance(query, str) or not query.strip():
            raise ValidationError("'query' must be non-empty text.")
        if not isinstance(limit, int) or isinstance(limit, bool) or not (
            1 <= limit <= MAX_SEARCH_LIMIT
        ):
            raise ValidationError(
                f"'limit' must be a whole number from 1 to {MAX_SEARCH_LIMIT}."
            )
        if not isinstance(case_sensitive, bool):
            raise ValidationError("'case_sensitive' must be true or false.")
        needle = query if case_sensitive else query.casefold()
        matches = []
        skipped_too_large = []
        for rel, real in self.note_files(folder):
            if os.path.getsize(real) > MAX_SEARCH_FILE_BYTES:
                skipped_too_large.append({"path": rel, "size_bytes": os.path.getsize(real)})
                continue
            with open(real, "r", encoding="utf-8", errors="replace") as handle:
                for line_number, line in enumerate(handle, start=1):
                    haystack = line if case_sensitive else line.casefold()
                    if needle in haystack:
                        matches.append(
                            {"path": rel, "line": line_number, "text": line.rstrip()[:240]}
                        )
                    if len(matches) >= limit:
                        break
                if len(matches) >= limit:
                    break
        return {
            "query": query,
            "folder": normalize_folder_path(folder),
            "case_sensitive": case_sensitive,
            "count": len(matches),
            "matches": matches,
            "limit_reached": len(matches) >= limit,
            "skipped_too_large": skipped_too_large,
        }

    def _backup_existing(self, rel):
        source = os.path.join(self.root, *rel.split("/"))
        if not os.path.exists(source):
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
        dir_parts = [BACKUP_DIRNAME, stamp]
        parent = posixpath.dirname(rel)
        if parent:
            dir_parts += parent.split("/")
        backup_dir = os.path.join(self.root, *dir_parts)
        os.makedirs(backup_dir, exist_ok=True)
        base_name = posixpath.basename(rel)
        counter = 1
        candidate = os.path.join(backup_dir, base_name)
        while os.path.exists(candidate):
            candidate = os.path.join(backup_dir, f"{base_name}.{counter}")
            counter += 1
        shutil.copy2(source, candidate)
        return "/".join(dir_parts + [os.path.basename(candidate)])

    def write_note(self, path, content):
        if not isinstance(content, str):
            raise ValidationError("Note content must be text.")
        rel, real = self.note_file(path)
        try:
            with open(real, "rb") as handle:
                existing = handle.read()
        except FileNotFoundError:
            existing = None
        except IsADirectoryError:
            raise NotesError(f"'{rel}' is a folder, not a note.")
        data = content.encode("utf-8")
        if existing == data:
            return {
                "path": rel,
                "created": False,
                "bytes_written": 0,
                "backup": None,
                "unchanged": True,
            }
        backup = self._backup_existing(rel)
        os.makedirs(os.path.dirname(real), exist_ok=True)
        tmp_path = real + f".{os.getpid()}.tmp"
        with open(tmp_path, "wb") as handle:
            handle.write(data)
        os.replace(tmp_path, real)
        return {
            "path": rel,
            "created": existing is None,
            "bytes_written": len(data),
            "backup": backup,
            "unchanged": False,
        }

    def append_note(self, path, content):
        if not isinstance(content, str):
            raise ValidationError("Note content must be text.")
        rel, real = self.note_file(path)
        exists = os.path.isfile(real)
        if not exists and os.path.isdir(real):
            raise NotesError(f"'{rel}' is a folder, not a note.")
        if content == "":
            return {
                "path": rel,
                "bytes_appended": 0,
                "backup": None,
                "created": not exists,
            }
        chunk = content if content.endswith("\n") else content + "\n"
        data = chunk.encode("utf-8")
        prefix = b""
        if exists and os.path.getsize(real) > 0:
            with open(real, "rb") as handle:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    prefix = b"\n"
        backup = self._backup_existing(rel)
        with open(real, "ab") as handle:
            handle.write(prefix + data)
        return {
            "path": rel,
            "bytes_appended": len(prefix) + len(data),
            "backup": backup,
            "created": not exists,
        }


def tool_specs():
    return [
        {
            "name": "list_notes",
            "description": (
                "List the Markdown notes the server can see in the vault, or "
                "inside one subfolder. Use this first to learn what notes "
                "exist and how they are spelled. Example arguments: {} or "
                "{'folder': 'journal'}. Hidden folders such as .backups are "
                "never shown."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "Subfolder to list, relative to the vault root (e.g. 'ideas'). Omit to list the whole vault.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_LIST_LIMIT,
                        "default": DEFAULT_LIST_LIMIT,
                        "description": f"Maximum number of notes to return (1-{MAX_LIST_LIMIT}; default {DEFAULT_LIST_LIMIT}).",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "search_notes",
            "description": (
                "Search the text of all visible notes for a word or phrase "
                "(case-insensitive by default) and get matching lines with "
                "the note path and line number. Example arguments: "
                "{'query': 'backup'} or {'query': 'streak', 'folder': 'ideas'}."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Plain text to look for inside the notes.",
                    },
                    "folder": {
                        "type": "string",
                        "description": "Optionally restrict the search to one subfolder (e.g. 'journal').",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "default": False,
                        "description": "Set true to make the search case-sensitive.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_SEARCH_LIMIT,
                        "default": DEFAULT_SEARCH_LIMIT,
                        "description": f"Maximum number of matching lines to return (1-{MAX_SEARCH_LIMIT}; default {DEFAULT_SEARCH_LIMIT}).",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "read_note",
            "description": (
                "Read one note and return its full text. Use a path exactly "
                "as list_notes or search_notes showed it, for example "
                "{'path': 'getting-started.md'}. Very large notes are trimmed."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Note path relative to the vault root, e.g. 'ideas/chess-puzzle-tracker.md'.",
                    }
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "write_note",
            "description": (
                "Create a new note or replace an existing one with new text. "
                "If the note already exists, a backup of the old version is "
                "saved under .backups/ first. Writes only work inside the "
                "vault and its allowed folders. Example: {'path': 'todo.md', "
                "'content': '# To do\\n- say hi to the assistant\\n'} — parent "
                "folders (like 'ideas/') are created automatically."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Note path relative to the vault root, must end in .md or .markdown.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The complete new Markdown text for the note.",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
        {
            "name": "append_note",
            "description": (
                "Add text to the end of a note (creating it if needed; an "
                "existing note is backed up first). Great for journal lines "
                "and quick captures. Example: {'path': "
                "'journal/2026-10-22.md', 'content': 'Tried search_notes, it worked.\\n'}"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Note path relative to the vault root, must end in .md or .markdown.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text to add at the end of the note.",
                    },
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
        },
    ]


_TYPE_TABLE = {"string": str, "boolean": bool, "integer": int, "number": (int, float)}


def validate_arguments(schema, arguments):
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ValidationError("Tool arguments must be a JSON object.")
    for name, rule in schema["properties"].items():
        if name not in arguments:
            continue
        value = arguments[name]
        expected = _TYPE_TABLE[rule["type"]]
        invalid = not isinstance(value, expected)
        if not invalid and rule["type"] in ("integer", "number") and isinstance(value, bool):
            invalid = True
        if invalid:
            raise ValidationError(f"Argument '{name}' must be of type {rule['type']}.")
        minimum = rule.get("minimum")
        maximum = rule.get("maximum")
        if minimum is not None and value < minimum:
            raise ValidationError(f"Argument '{name}' must be at least {minimum}.")
        if maximum is not None and value > maximum:
            raise ValidationError(f"Argument '{name}' must be at most {maximum}.")
    for name in schema.get("required", []):
        if name not in arguments:
            raise ValidationError(f"Argument '{name}' is required for this tool.")
    extras = sorted(set(arguments) - set(schema["properties"]))
    if extras:
        raise ValidationError(
            "Unknown argument(s): " + ", ".join(extras) + "."
        )
    return dict(arguments)


def _rpc_result(id_value, result):
    return {"jsonrpc": "2.0", "id": id_value, "result": result}


def _rpc_error(id_value, code, message):
    return {
        "jsonrpc": "2.0",
        "id": id_value if id_value is not None else None,
        "error": {"code": code, "message": message},
    }


SERVER_INSTRUCTIONS = (
    "This server exposes a local Markdown notes vault. Use list_notes to see "
    "what exists, search_notes to find text across notes, read_note to open a "
    "note, write_note to create or replace a note, and append_note to add to "
    "the end of a note. Paths are relative to the vault root and must end in "
    ".md or .markdown. Hidden items (including .backups) are off-limits, and "
    "every edit to an existing note is backed up first. Do not invent paths; "
    "use ones from list_notes or search_notes."
)


class Server:
    """Dispatches MCP JSON-RPC messages against a Vault."""

    def __init__(self, vault):
        self.vault = vault
        self.tool_specs = tool_specs()
        self.schemas = {spec["name"]: spec["inputSchema"] for spec in self.tool_specs}
        self.registry = {
            "list_notes": vault.list_notes,
            "search_notes": vault.search_notes,
            "read_note": vault.read_note,
            "write_note": vault.write_note,
            "append_note": vault.append_note,
        }

    def process(self, message):
        """Return a JSON-RPC response dict, or None for notifications."""
        if not isinstance(message, dict):
            return _rpc_error(None, -32600, "Invalid request: expected a JSON object.")
        if message.get("jsonrpc") != "2.0":
            return _rpc_error(
                message.get("id"),
                -32600,
                "Invalid request: the 'jsonrpc' field must be exactly \"2.0\".",
            )
        id_value = message.get("id")
        if "id" in message and id_value is not None and isinstance(id_value, bool):
            return _rpc_error(None, -32600, "Invalid request: 'id' must be a string, number, or null.")
        if "id" in message and id_value is not None and not isinstance(id_value, (str, int)):
            return _rpc_error(None, -32600, "Invalid request: 'id' must be a string, number, or null.")
        method = message.get("method")
        if not isinstance(method, str) or not method:
            if "id" in message:
                return _rpc_error(id_value, -32600, "Invalid request: missing 'method'.")
            return None
        is_notification = "id" not in message

        if method == "initialize":
            return self._initialize(id_value, message.get("params"))
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return None if is_notification else _rpc_result(id_value, {})
        if method == "tools/list":
            return None if is_notification else _rpc_result(id_value, {"tools": self.tool_specs})
        if method == "tools/call":
            return None if is_notification else self._tools_call(id_value, message.get("params"))

        if is_notification:
            return None
        return _rpc_error(id_value, -32601, f"Method not found: {method}")

    def _initialize(self, id_value, params):
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        version = (
            requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
        )
        return _rpc_result(
            id_value,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": SERVER_INSTRUCTIONS,
            },
        )

    def _tools_call(self, id_value, params):
        if not isinstance(params, dict):
            return _rpc_error(id_value, -32602, "Invalid params: expected an object with 'name'.")
        name = params.get("name")
        if not isinstance(name, str) or name not in self.registry:
            available = ", ".join(sorted(self.registry))
            return _rpc_error(
                id_value,
                -32602,
                f"Unknown tool: {name!r}. Available tools: {available}.",
            )
        try:
            arguments = validate_arguments(self.schemas[name], params.get("arguments"))
        except ValidationError as exc:
            return _rpc_error(id_value, -32602, str(exc))
        handler = self.registry[name]
        try:
            payload = handler(**arguments)
        except ValidationError as exc:
            return _rpc_error(id_value, -32602, str(exc))
        except NotesError as exc:
            log(f"tool '{name}' reported: {exc}")
            return _rpc_result(
                id_value,
                {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "isError": True,
                },
            )
        except Exception as exc:
            log(f"tool '{name}' failed unexpectedly: {type(exc).__name__}: {exc}")
            return _rpc_result(
                id_value,
                {
                    "content": [
                        {"type": "text", "text": f"Error: {type(exc).__name__}: {exc}"}
                    ],
                    "isError": True,
                },
            )
        log(f"tool '{name}' ok")
        return _rpc_result(
            id_value,
            {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(payload, indent=2, ensure_ascii=False),
                    }
                ],
                "isError": False,
            },
        )


def serve(vault, input_stream=None, output_stream=None):
    """Read newline-delimited JSON-RPC from stdin, write responses to stdout."""
    input_stream = input_stream if input_stream is not None else sys.stdin.buffer
    output_stream = output_stream if output_stream is not None else sys.stdout.buffer
    server = Server(vault)

    def send(payload):
        line = json.dumps(payload, ensure_ascii=True).encode("utf-8") + b"\n"
        output_stream.write(line)
        output_stream.flush()

    for raw_line in input_stream:
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            message = json.loads(stripped.decode("utf-8"))
        except UnicodeDecodeError:
            send(_rpc_error(None, -32700, "Parse error: line is not valid UTF-8."))
            continue
        except json.JSONDecodeError as exc:
            send(_rpc_error(None, -32700, f"Parse error: {exc.msg}"))
            continue
        try:
            response = server.process(message)
        except Exception as exc:
            log(f"internal error while handling a message: {type(exc).__name__}: {exc}")
            failed_id = message.get("id") if isinstance(message, dict) else None
            response = _rpc_error(failed_id, -32603, "Internal error while handling the request.")
        if response is not None:
            send(response)


def parse_arguments(argv):
    parser = argparse.ArgumentParser(
        prog=SERVER_NAME,
        description=(
            "A small MCP (Model Context Protocol) server that exposes a "
            "folder of Markdown notes over stdio. Python 3 standard library "
            "only; every access is sandboxed to that one folder."
        ),
    )
    parser.add_argument(
        "--vault",
        default=os.environ.get("NOTES_VAULT", DEFAULT_VAULT),
        metavar="PATH",
        help="folder holding your Markdown notes (default: the shipped ./sample_vault).",
    )
    parser.add_argument(
        "--allow-folder",
        action="append",
        default=None,
        metavar="FOLDER",
        dest="allow_folders",
        help=(
            "restrict the tools to a subfolder, relative to the vault root "
            "(e.g. --allow-folder ideas). Pass several times to allow "
            "several folders. Without this flag the whole vault is allowed."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{SERVER_NAME} {SERVER_VERSION}",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_arguments(argv)
    if args.allow_folders:
        allow = args.allow_folders
    else:
        env_value = os.environ.get("NOTES_ALLOWED_FOLDERS", "")
        allow = [part.strip() for part in env_value.split(",") if part.strip()] or None
    try:
        vault = Vault(args.vault, allowed_folders=allow)
    except NotesError as exc:
        log("startup failed:", exc)
        return 2
    readable = ["(full vault)" if f == "" else f for f in vault.allowed_folders]
    log(f"starting | vault={vault.root} | allowed folders: {', '.join(readable)}")
    try:
        serve(vault)
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        log("stopping (interrupted).")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
