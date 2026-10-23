# Backups and restore, plain English

## What happens when a note is saved

Before the server overwrites or edits an existing note, it copies the
current version into this folder first:

    .backups/<timestamp>/<original name>

For example:

    .backups/2026-10-22T171900/journal/2026-10-22.md

Timestamps are UTC. If two saves happen in the same second, the second one
gets a number appended (`journal/2026-10-22.md.1`). Creating a brand-new
note needs no backup — there is nothing to back up yet.

## How to restore an older version

1. Open the `.backups` folder in your file manager. It starts with a dot,
   so it may be hidden — switch on "show hidden files" if you don't see it.
2. Find the timestamp folder closest to the version you want back.
3. Copy the note out of the timestamp folder and put it back where the
   live one lives, replacing the newer version.

Two reassuring details:

- Backups live inside the vault, so they travel with the folder when you
  back it up or sync it.
- The MCP tools themselves cannot read or edit `.backups` — that folder
  exists for *you*, not for the assistant.
