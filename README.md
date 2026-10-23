# Notes Vault MCP Server

A tiny, local-first **MCP server** that gives an AI assistant safe, friendly
access to one folder of Markdown notes — no cloud, no API keys, no
dependencies beyond Python itself.

Built by **Asdale Dynamics, LLC** (Dylan Asdale's company) as a practical,
beginner-friendly companion for *The Massive AI Manual* and the plain-English
AI guides at [dylanasdale.github.io](https://dylanasdale.github.io).

---

## What is this, in plain English?

**MCP** (Model Context Protocol) is a standard way for AI assistants to use
"tools" — think of it as the universal adapter plug between an assistant and
the things you actually work with. An **MCP server** is a small program that
offers those tools. This one offers **your notes**.

When the assistant connects, it gains exactly five abilities:

| Tool | What it does |
| --- | --- |
| `list_notes` | List every note the server can see (optionally inside one subfolder) |
| `search_notes` | Find text across all notes, with note path + line numbers |
| `read_note` | Open one note and return its full text |
| `write_note` | Create a note, or replace an existing one (backed up first) |
| `append_note` | Add text to the end of a note (backed up first) |

And one hard promise: **nothing outside the vault folder is touched.** See
[Safety rails](#safety-rails) below.

## Requirements

- **Python 3.9+** — that's it. Standard library only, no `pip install`.
- Optional: **Node.js 18+** if you want the browser-based Inspector described
  in the next section.

## Quick start (30 seconds with the sample vault)

A sample vault ships with this project, so you can try everything with zero
setup:

```bash
python3 notes_mcp_server.py
```

When it starts you'll see one log line and then... it looks stuck. That's
normal! An MCP stdio server sits quietly waiting for a client to talk to it.
Press **Ctrl+C** to stop when you want out.

On Windows, use `python` (or `py`) instead of `python3`.

## Testing it with the MCP Inspector (recommended first stop)

The [MCP Inspector](https://github.com/modelcontextprotocol/inspector) is an
official browser-based test bench for MCP servers. In one terminal:

```bash
npx @modelcontextprotocol/inspector python3 notes_mcp_server.py
```

(A browser tab opens, usually `http://localhost:6274`.) Then:

1. Click **Connect**. If everything is wired right you'll see the server's
   name (`notes-vault-mcp`) and a green status.
2. Open the **Tools** tab. You should see the five tools with friendly
   descriptions pulled straight from this server.
3. Try `list_notes` first — leave the form empty and hit **Run**. You'll get
   a JSON list of the sample notes (`getting-started.md` and friends).
4. Try `read_note` with `path` set to `getting-started.md`.
5. Try `search_notes` with `query` set to `backup`. Every matching line
   comes back with its note and line number.
6. Try `write_note` with `path` `playground.md` and some markdown in
   `content` — then read it back with `read_note`. Check the vault folder:
   your file is there, and nothing else changed.
7. Try to be naughty: `read_note` with `path` `../escape.md`. The server
   answers with a friendly error instead of leaving the vault. Same for
   `/etc/passwd`, `plain.txt`, or `.backups/...` — all refused.

Headless alternative (no browser), same Inspector package:

```bash
npx @modelcontextprotocol/inspector --cli python3 notes_mcp_server.py \
  --method tools/call --tool-name search_notes --tool-arg query=backup
```

## Running against your own notes

```bash
mkdir ~/Documents/notes          # any folder you like
python3 notes_mcp_server.py --vault ~/Documents/notes
```

or point a client at the server permanently (see the config snippet below).

Paths you pass to the tools are always **relative to the vault root**, e.g.
`journal/oct.md`. Writing into a subfolder that doesn't exist yet is fine —
the parent folders are created for you.

## Connecting it to Claude Desktop (or any MCP client)

Add it to your MCP client's server config. Example for Claude Desktop
(`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "notes-vault": {
      "command": "python3",
      "args": ["--vault", "/absolute/path/to/your/notes"]
    }
  }
}
```

An absolute `--vault` path avoids surprises, since your assistant won't know
what directory the server was launched from.

## Safety rails

Every request passes through the same guards; there are no exceptions,
no "trusted mode":

- **Path safety** — paths must be relative to the vault. Absolute paths,
  `..` segments, Windows style `C:/...`, and colons are refused outright.
- **Symlink seals** — if a link inside the vault would resolve anywhere
  outside it, every tool refuses the path (verified each call via `realpath`).
- **Markdown only** — only `.md` and `.markdown` files are read or written.
- **Hidden items are sealed** — anything starting with `.` (including the
  `.backups` folder, and things like `.git/` or `.obsidian/`) is invisible
  to the tools. The assistant cannot see or modify your backup history.
- **Folder allowlist (optional)** — restrict the *entire* server to
  specific subfolders:

  ```bash
  # The assistant may only touch ideas/, and anything inside it:
  python3 notes_mcp_server.py --vault ~/notes --allow-folder ideas

  # Give it several folders by repeating the flag:
  python3 notes_mcp_server.py --vault ~/notes --allow-folder ideas --allow-folder journal

  # Or via environment variables:
  export NOTES_VAULT=~/notes
  export NOTES_ALLOWED_FOLDERS=ideas,journal
  ```

  With an allowlist active, listing or searching other folders is refused
  too — the assistant simply can't see what it isn't allowed to see.
- **Backup before write** — any write that would change an existing note
  copies the old version to `.backups/<UTC timestamp>/<original name>`
  first. Restoring is just copying the file back into place. The sample
  vault includes `how-to/backup-and-restore.md` explaining this to a
  future-you or to an assistant that reads vault contents.

## Smoke test and unit tests

Both verify without touching your real notes.

**Smoke test** — launches the real server as a subprocess and runs a full
MCP conversation against a throwaway copy of the sample vault: handshake,
tool listing, every tool round-trip, and the security refusals:

```bash
python3 smoke_test.py
```

You want this exact ending:

```
SMOKE TEST PASSED (14 checks green)
```

**Unit tests** — 54 checks covering tool round-trips and the security
rails (path traversal, symlinks, allowlists, backups, hidden folders):

```bash
python3 -m unittest discover -s tests -t . -v
```

You want:

```
Ran 54 tests in 0.03s

OK
```

## Project layout

```
notes-mcp-server/
├── notes_mcp_server.py       the whole server (stdlib only)
├── smoke_test.py             end-to-end protocol + safety check
├── tests/                    unit tests (unittest, stdlib)
├── sample_vault/             try-me notes vault
│   ├── getting-started.md
│   ├── how-to/backup-and-restore.md
│   ├── ideas/chess-puzzle-tracker.md
│   ├── journal/2026-10-22.md
│   └── writing/book-outline.md
├── README.md
├── LICENSE
└── STATUS.md                 build & verification status
```

## Troubleshooting

- **"Vault folder does not exist"** — the `--vault` path is wrong. Create
  the folder first (`mkdir`) or use the shipped `sample_vault`.
- **The server "hangs"** — it's waiting for an MCP client on stdin. Start
  it from the Inspector or an MCP client, or just Ctrl+C to stop.
- **"Folder 'x' is not in the allowlist"** — you started the server with
  `--allow-folder` / `NOTES_ALLOWED_FOLDERS`, and the tool asked for a
  folder outside it. Add the folder to the config or restart without it.
- **Inspector port or layout questions** — the Inspector has its own docs at
  <https://github.com/modelcontextprotocol/inspector>.
- **A note is missing an older version** — look in the vault's `.backups`
  folder (show hidden files) and copy the timestamped snapshot back.

## License and credits

MIT License — see [LICENSE](LICENSE).
Copyright (c) 2026 Asdale Dynamics, LLC.

This project is part of Asdale Dynamics' beginner-friendly AI collection:
plain English, step by step, never condescending. For more guides, see
Dylan Asdale's *The Massive AI Manual* and
[dylanasdale.github.io](https://dylanasdale.github.io).
