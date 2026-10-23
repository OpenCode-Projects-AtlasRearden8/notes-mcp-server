# Getting started with this vault

Welcome! This folder is a demo "notes vault" for the notes-vault MCP server.
An AI assistant that connects to this server can list, search, read, and
edit the Markdown notes in here — and touch nothing else on your machine.

## Quick tour

- `getting-started.md` — this file
- `how-to/backup-and-restore.md` — how the automatic backups work
- `ideas/chess-puzzle-tracker.md` — a scratchpad project idea
- `journal/2026-10-22.md` — a one-screen journal entry
- `writing/book-outline.md` — a plain-English book outline draft

## Try asking your assistant

- "List my notes."
- "Search my notes for *backup*."
- "Read `ideas/chess-puzzle-tracker.md`."
- "Append 'hello!' to the journal note."

## The safety rails, in one breath

Paths always stay inside this vault, only `.md` / `.markdown` files are
touched, hidden folders (like `.backups`) are off-limits, and every write
to an existing note is backed up first. See
`how-to/backup-and-restore.md` for the friendly version.
