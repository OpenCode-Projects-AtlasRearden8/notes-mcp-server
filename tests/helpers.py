"""Shared helpers for the notes-vault MCP server unit tests."""

import os
import sys
import tempfile
from contextlib import contextmanager

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import notes_mcp_server as nms  # noqa: E402

DEFAULT_SEED = {
    "getting-started.md": (
        "# Getting started\n\nWelcome. Search for the word backup to find\n"
        "restore steps.\n"
    ),
    "ideas/chess.md": (
        "# Chess idea\n\nTrack puzzle streaks in daily.md.\n"
    ),
    "journal/2026-10-22.md": (
        "# Today\n\nDate: 2026-10-22\n\nTested the notes vault MCP server.\n"
    ),
}


def seed_file(root, rel, content):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


@contextmanager
def temp_vault(seed=None, allowed=None):
    with tempfile.TemporaryDirectory() as tmp:
        for rel, text in (seed if seed is not None else DEFAULT_SEED).items():
            seed_file(tmp, rel, text)
        yield nms.Vault(tmp, allowed_folders=allowed), tmp


def find_backups(root):
    backup_root = os.path.join(root, nms.BACKUP_DIRNAME)
    found = []
    if not os.path.isdir(backup_root):
        return found
    for dirpath, _dirnames, filenames in os.walk(backup_root):
        for name in filenames:
            full = os.path.join(dirpath, name)
            found.append(
                (os.path.relpath(full, backup_root).replace(os.sep, "/"), full)
            )
    return sorted(found)


def read_file_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()
