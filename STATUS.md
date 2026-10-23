# STATUS.md — notes-vault-mcp

Status: **complete and verified**, 2026-10-23.

## What was built

A small MCP (Model Context Protocol) server that exposes a local Markdown
notes vault over stdio, written entirely with the Python standard library
(no dependencies, no API keys, no network calls at runtime).

- `notes_mcp_server.py` — the whole server. Newline-delimited JSON-RPC 2.0
  over stdio per the MCP transport spec; implements `initialize`,
  `ping`, `tools/list`, and `tools/call` for five tools:
  `list_notes`, `search_notes`, `read_note`, `write_note`, `append_note`.
- Safety rails, all enforced in one funnel:
  - **Path safety**: relative paths only; absolute paths, `..` segments,
    `C:/`-style and colon paths, NUL bytes refused; writes restricted to
    `.md`/`.markdown`.
  - **Symlink seals**: every path is `realpath`-checked against the vault
    root on each call, so links can't sneak out of the vault.
  - **Hidden items sealed**: anything starting with `.` (including
    `.backups/`, `.git/`, `.obsidian/`) is invisible to every tool.
  - **Folder allowlist**: optional `--allow-folder` (repeatable) or
    `NOTES_ALLOWED_FOLDERS` env var restricts listing, searching, reading,
    and writing to configured subfolders (nested folders of an allowed
    folder stay allowed). Default: the whole vault.
  - **Backup before write**: any write that changes an existing note first
    copies it to `.backups/<UTC timestamp>/<original name>` (with a numeric
    suffix if two saves land in the same second). Identical writes are
    no-ops with no backup. The `.backups` folder itself is not writable or
    readable through the tools.
  - **Guarded writes**: notes are written to a temp file in the same folder
    and atomically renamed, so a crash can't truncate an existing note.
- `sample_vault/` — five try-me notes (getting started, backup how-to,
  chess idea, journal entry, book outline) in four subfolders.
- `smoke_test.py` — stdlib-only end-to-end MCP client that launches the
  server as a subprocess on a throwaway vault copy and runs the full
  protocol conversation plus the security refusals.
- `tests/` — 54 unit tests (`unittest`, stdlib) covering tool round-trips,
  pagination/limits, casefold-aware search, and the security cases
  (traversal, absolute/colon paths, symlinks in and out, allowlists,
  hidden paths, backup behavior).
- `README.md` — plain-English docs: quick start, Inspector testing guide,
  client config, safety rails, test instructions, troubleshooting.
- `LICENSE` — MIT, Copyright (c) 2026 Asdale Dynamics, LLC.

## How to run it

```bash
# with the shipped sample vault
python3 notes_mcp_server.py

# with your own folder
python3 notes_mcp_server.py --vault /path/to/your/notes

# with a folder allowlist
python3 notes_mcp_server.py --vault /path/to/notes --allow-folder ideas

# interactive testing in the browser via the official Inspector
npx @modelcontextprotocol/inspector python3 notes_mcp_server.py

# headless Inspector check
npx @modelcontextprotocol/inspector --cli python3 notes_mcp_server.py \
  --method tools/call --tool-name search_notes --tool-arg query=backup
```

Claude Desktop-style config:

```json
{ "mcpServers": { "notes-vault": { "command": "python3",
  "args": ["--vault", "/absolute/path/to/notes"] } } }
```

## Verification results

All run from the project folder on Python 3.13.5, Linux.

| Check | Command | Result |
| --- | --- | --- |
| Unit tests | `python3 -m unittest discover -s tests -t .` | **Ran 54 tests — OK** |
| End-to-end smoke test | `python3 smoke_test.py` | **SMOKE TEST PASSED (14 checks green)** |
| CLI | `--version`, `--help`, bad `--vault` | clean exit, friendly startup error |
| Real MCP client (Inspector CLI), `tools/list` | `npx @modelcontextprotocol/inspector --cli python3 notes_mcp_server.py --method tools/list` | all five tools with schemas |
| Real client, search round trip | `--method tools/call --tool-name search_notes --tool-arg query=backup` | 13 matches with paths + line numbers, `isError: false` |
| Real client, traversal blocked | `--tool-name read_note --tool-arg path=../outside.md` | `isError: true`, "'..' is not allowed" |
| Real client, allowlist enforced | server started with `--allow-folder ideas` | `list_notes`/`read_note` inside `ideas` succeed; root note refused with allowlist error |
| Parse error handling | literal garbage line on stdin | `-32700` parse error response, server keeps running |

Errors surface the way MCP clients expect: bad *arguments* are protocol
errors (`-32602`), rule violations (missing note, forbidden path) are tool
results with `isError: true`, and the server never crashes on bad input.

## Known gaps / honest limitations

- **No delete or move tool** — by design (reduces blast radius); delete
  files yourself, or clear content with `write_note`.
- **Search is literal substring matching** — no regex, fuzzy, or
  frontmatter-aware search yet. Good enough for "which note mentioned X".
- **Results are capped** — search shows up to 200 matching lines, list up
  to 500 notes, reads cap at ~1 MB (`truncated: true` flags the cut).
  No pagination beyond those limits.
- **Single process, single writer** — no locking if two MCP clients write
  the same note at once (last write wins; backups still capture the
  pre-write state per process).
- **Folders are not created by listing/searching** — only `write_note` /
  `append_note` create parent folders on demand.
- **Not multi-tenant** — the vault is as safe as the user account running
  the process; this is a local single-user tool, not a sandbox for running
  untrusted server-side code.
- **Inspector tested via its headless CLI mode** — the browser UI uses the
  same client under the hood, but was validated through the CLI in this
  environment.

## Suggested next steps (as ideas, not promises)

- A `delete_note` tool with its own trash-style backup.
- Frontmatter-aware search and editing (title/tags).
- An import-from-Obsidian getting-started note.
