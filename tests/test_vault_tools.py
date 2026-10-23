"""Unit tests for the Vault tool round-trips and security rails."""

import os
import tempfile
import unittest

try:
    from tests.helpers import (
        temp_vault,
        seed_file,
        find_backups,
        read_file_bytes,
        nms,
    )
except ImportError:
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tests.helpers import (
        temp_vault,
        seed_file,
        find_backups,
        read_file_bytes,
        nms,
    )


@unittest.skipIf(hasattr(os, "symlink") is False, "symlinks unavailable")
class SymlinkEscapeTests(unittest.TestCase):
    def setUp(self):
        self._stack = tempfile.TemporaryDirectory()
        self.outside = tempfile.mkdtemp(prefix="escape-")
        self.addCleanup(self._stack.cleanup)
        self.addCleanup(_rmtree, self.outside)

    def _linked_vault(self):
        root = self._stack.name
        vault = nms.Vault(root)
        return vault, root

    def test_symlink_file_escape_rejected(self):
        seed_file(self.outside, "target.md", "sneaky\n")
        vault, root = self._linked_vault()
        os.symlink(os.path.join(self.outside, "target.md"), os.path.join(root, "link.md"))
        with self.assertRaises(nms.NotesError) as caught:
            vault.read_note("link.md")
        self.assertIn("outside the vault", str(caught.exception))
        with self.assertRaises(nms.NotesError) as caught:
            vault.write_note("link.md", "hi")
        self.assertIn("outside the vault", str(caught.exception))
        self.assertEqual(find_backups(root), [])

    def test_symlink_directory_escape_rejected(self):
        seed_file(self.outside, "secret.md", "sneaky\n")
        vault, root = self._linked_vault()
        os.symlink(self.outside, os.path.join(root, "shady"))
        with self.assertRaises(nms.NotesError) as caught:
            vault.read_note("shady/secret.md")
        self.assertIn("outside the vault", str(caught.exception))
        with self.assertRaises(nms.NotesError) as caught:
            list(vault.note_files("shady"))
        self.assertIn("outside the vault", str(caught.exception))

    def test_listing_does_not_follow_symlinked_folders(self):
        seed_file(self.outside, "secret.md", "sneaky\n")
        vault, root = self._linked_vault()
        os.symlink(self.outside, os.path.join(root, "shady"))
        paths = [note["path"] for note in vault.list_notes()["notes"]]
        self.assertFalse(any("shady" in p or "secret" in p for p in paths))


def _rmtree(path):
    import shutil

    shutil.rmtree(path, ignore_errors=True)


class RoundTripTests(unittest.TestCase):
    def test_write_then_read_returns_the_same_content(self):
        with temp_vault() as (vault, root):
            result = vault.write_note("todo.md", "# Todo\n\n- try the server\n")
            self.assertEqual(result["path"], "todo.md")
            self.assertTrue(result["created"])
            self.assertIsNone(result["backup"])
            read_back = vault.read_note("todo.md")
            self.assertEqual(read_back["content"], "# Todo\n\n- try the server\n")
            self.assertFalse(read_back["truncated"])
            self.assertEqual(read_back["size_bytes"], len(read_back["content"].encode("utf-8")))

    def test_write_creates_parent_folders_and_unicode_survives(self):
        with temp_vault() as (vault, root):
            text = "# Japan\n\n\u60f7\u304c\u3057\u306e\u65e5\u8a18\n"
            vault.write_note("deep/travel/tokyo.md", text)
            self.assertEqual(vault.read_note("deep/travel/tokyo.md")["content"], text)

    def test_append_creates_new_note_and_joins_existing_note(self):
        with temp_vault() as (vault, root):
            created = vault.append_note("journal/morning.md", "first line")
            self.assertTrue(created["created"])
            self.assertEqual(vault.read_note("journal/morning.md")["content"], "first line\n")

            vault.write_note("journal/join.md", "be kind")
            vault.append_note("journal/join.md", "stay curious")
            self.assertEqual(
                vault.read_note("journal/join.md")["content"],
                "be kind\nstay curious\n",
            )

    def test_list_notes_lists_sorted_md_files_and_skips_hidden_and_non_md(self):
        with temp_vault() as (vault, root):
            seed_file(root, ".obsidian/config.json", "{}")
            seed_file(root, "readme.txt", "not a note")
            listed = vault.list_notes()
            paths = [note["path"] for note in listed["notes"]]
            self.assertEqual(
                paths,
                ["getting-started.md", "ideas/chess.md", "journal/2026-10-22.md"],
            )
            self.assertTrue(listed["complete"])
            self.assertEqual(listed["count"], 3)
            for note in listed["notes"]:
                self.assertIn("size_bytes", note)
                self.assertIn("modified_utc", note)

    def test_list_notes_with_folder_filter(self):
        with temp_vault() as (vault, root):
            listed = vault.list_notes(folder="ideas")
            self.assertEqual([note["path"] for note in listed["notes"]], ["ideas/chess.md"])

    def test_list_notes_never_shows_backups(self):
        with temp_vault() as (vault, root):
            vault.write_note("todo.md", "one\n")
            vault.write_note("todo.md", "two\n")
            self.assertTrue(find_backups(root))
            paths = [note["path"] for note in vault.list_notes()["notes"]]
            self.assertFalse(any(p.startswith(nms.BACKUP_DIRNAME) for p in paths))

    def test_search_finds_text_with_line_numbers_casefold_aware(self):
        with temp_vault() as (vault, root):
            result = vault.search_notes("SERVER")
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["matches"][0]["path"], "journal/2026-10-22.md")
            self.assertEqual(result["matches"][0]["line"], 5)

            vault.write_note("vocab.md", "die Stra\u00dfe\n")
            german = vault.search_notes("STRASSE")
            self.assertEqual(german["count"], 1)
            self.assertEqual(german["matches"][0]["path"], "vocab.md")

    def test_search_folder_filter_and_case_sensitive(self):
        with temp_vault() as (vault, root):
            only_ideas = vault.search_notes("streak", folder="ideas")
            self.assertEqual(only_ideas["count"], 1)
            self.assertEqual(only_ideas["matches"][0]["path"], "ideas/chess.md")

            sensitive = vault.search_notes("mcp", case_sensitive=True)
            self.assertEqual(sensitive["count"], 0)
            insensitive = vault.search_notes("mcp")
            self.assertEqual(insensitive["count"], 1)
            self.assertEqual(insensitive["matches"][0]["path"], "journal/2026-10-22.md")

    def test_search_respects_limit_and_reports_it(self):
        seed = {f"bulk/file{number}.md": f"match me {number}\n" for number in range(40)}
        seed["oneliner.md"] = "match me\n" * 70
        with temp_vault(seed=seed) as (vault, root):
            result = vault.search_notes("match me", limit=10)
            self.assertEqual(result["count"], 10)
            self.assertTrue(result["limit_reached"])

    def test_list_notes_limit_pages_through_vault(self):
        seed = {f"bulk/file{number}.md": "word\n" for number in range(30)}
        with temp_vault(seed=seed) as (vault, root):
            result = vault.list_notes(limit=5)
            self.assertEqual(result["count"], 5)
            self.assertFalse(result["complete"])

    def test_missing_folder_gives_a_friendly_error(self):
        with temp_vault() as (vault, root):
            with self.assertRaises(nms.NotesError) as caught:
                vault.list_notes(folder="nowhere")
            self.assertIn("does not exist", str(caught.exception))


class SecurityTests(unittest.TestCase):
    def test_absolute_paths_rejected(self):
        with temp_vault() as (vault, root):
            for ugly in ["/etc/notes.md", "\\vault\\notes.md"]:
                with self.assertRaises(nms.NotesError) as caught:
                    vault.read_note(ugly)
                self.assertIn("absolute", str(caught.exception))
                self.assertIn("relative", str(caught.exception))

    def test_drive_letter_and_colon_paths_rejected(self):
        with temp_vault() as (vault, root):
            for ugly in ["C:/notes.md", "weird:name.md", "https:/example.com/notes.md"]:
                with self.assertRaises(nms.NotesError):
                    vault.read_note(ugly)

    def test_path_traversal_rejected(self):
        with temp_vault() as (vault, root):
            for ugly in ["../outside.md", "ideas/../../outside.md", "ideas/./../outside.md"]:
                with self.assertRaises(nms.NotesError) as caught:
                    vault.read_note(ugly)
                self.assertIn("'..' is not allowed", str(caught.exception))

    def test_hidden_paths_rejected_including_backups_folder(self):
        with temp_vault() as (vault, root):
            for ugly in [".secret.md", "ideas/.hidden.md", ".backups/todo.md"]:
                with self.assertRaises(nms.NotesError) as caught:
                    vault.write_note(ugly, "hi")
                self.assertIn("Hidden items", str(caught.exception))
            with self.assertRaises(nms.NotesError) as caught:
                vault.read_note(".backups/todo.md")
            self.assertIn("Hidden items", str(caught.exception))

    def test_non_markdown_extension_rejected(self):
        with temp_vault() as (vault, root):
            seed_file(root, "plain.txt", "raw")
            actions = [
                lambda: vault.read_note("plain.txt"),
                lambda: vault.write_note("plain.txt", "hi"),
                lambda: vault.write_note("script.sh", "#!/bin/sh"),
                lambda: vault.append_note("plain.txt", "hi"),
            ]
            for action in actions:
                with self.assertRaises(nms.NotesError) as caught:
                    action()
                self.assertIn("Markdown", str(caught.exception))

    def test_reading_a_folder_with_md_suffix_is_called_out(self):
        with temp_vault() as (vault, root):
            os.makedirs(os.path.join(root, "tricky.md"))
            with self.assertRaises(nms.NotesError) as caught:
                vault.read_note("tricky.md")
            self.assertIn("folder, not a note", str(caught.exception))

    def test_allowlist_blocks_read_and_write_outside_allowed_folders(self):
        with temp_vault(allowed=["ideas"]) as (vault, root):
            with self.assertRaises(nms.NotesError) as caught:
                vault.read_note("getting-started.md")
            self.assertIn("allowlist", str(caught.exception))
            with self.assertRaises(nms.NotesError):
                vault.write_note("todo.md", "hi")
            nested = vault.write_note("ideas/deep/new.md", "# ok\n")
            self.assertTrue(nested["created"])
            self.assertEqual(vault.read_note("ideas/chess.md")["path"], "ideas/chess.md")

    def test_allowlist_lets_root_note_paths_be_blocked_but_subfolder_work(self):
        with temp_vault(allowed=[""]) as (vault, root):
            vault.write_note("root.md", "fine\n")
            self.assertEqual(vault.read_note("root.md")["content"], "fine\n")

    def test_allowlist_blocks_search_and_listing_of_other_folders(self):
        with temp_vault(allowed=["ideas"]) as (vault, root):
            with self.assertRaises(nms.NotesError):
                vault.search_notes("backup", folder="journal")
            with self.assertRaises(nms.NotesError):
                vault.search_notes("backup")
            with self.assertRaises(nms.NotesError):
                vault.list_notes()
            self.assertEqual(vault.list_notes(folder="ideas")["count"], 1)

    def test_windows_style_relative_path_is_normalized(self):
        with temp_vault() as (vault, root):
            backslash = vault.read_note("ideas\\chess.md")
            self.assertEqual(backslash["path"], "ideas/chess.md")

    def test_empty_paths_rejected(self):
        with temp_vault() as (vault, root):
            for ugly in ["", "   ", "."]:
                with self.assertRaises(nms.NotesError):
                    vault.read_note(ugly)

    def test_write_content_must_be_text(self):
        with temp_vault() as (vault, root):
            with self.assertRaises(nms.ValidationError):
                vault.write_note("todo.md", 42)

    def test_allowlist_config_rejects_sneaky_entries(self):
        with self.assertRaises(nms.NotesError):
            nms.Vault(tempfile.mkdtemp(), allowed_folders=["../sneaky"])


class BackupTests(unittest.TestCase):
    def test_overwrite_saves_backup_with_previous_content(self):
        with temp_vault() as (vault, root):
            original = "# Todo\n\nfirst version"
            vault.write_note("todo.md", original)
            replaced = vault.write_note("todo.md", "# Todo\n\nsecond version")
            self.assertFalse(replaced["created"])
            backup_rel, backup_full = find_backups(root)[0]
            self.assertIn("todo.md", backup_rel)
            self.assertEqual(replaced["backup"], nms.BACKUP_DIRNAME + "/" + backup_rel)
            self.assertEqual(read_file_bytes(backup_full).decode("utf-8"), original)

    def test_identical_write_is_unchanged_and_backup_free(self):
        with temp_vault() as (vault, root):
            vault.write_note("todo.md", "same\n")
            result = vault.write_note("todo.md", "same\n")
            self.assertTrue(result["unchanged"])
            self.assertEqual(result["bytes_written"], 0)
            self.assertIsNone(result["backup"])
            self.assertEqual(find_backups(root), [])

    def test_append_backs_up_existing_note(self):
        with temp_vault() as (vault, root):
            before = vault.read_note("journal/2026-10-22.md")["content"]
            result = vault.append_note("journal/2026-10-22.md", "one more line\n")
            snapshot, backup = find_backups(root)[0]
            self.assertTrue(result["backup"].startswith(nms.BACKUP_DIRNAME + "/"))
            self.assertIn("/journal/2026-10-22.md", result["backup"])
            with open(backup, "r", encoding="utf-8", newline="") as handle:
                self.assertEqual(handle.read(), before)

    def test_backups_are_unreachable_by_the_tools(self):
        with temp_vault() as (vault, root):
            vault.write_note("todo.md", "before\n")
            vault.write_note("todo.md", "after\n")
            rel, full = find_backups(root)[0]
            self.assertEqual(read_file_bytes(full).decode("utf-8"), "before\n")
            with self.assertRaises(nms.NotesError) as caught:
                vault.read_note(f"{nms.BACKUP_DIRNAME}/{rel}")
            self.assertIn("Hidden items", str(caught.exception))

    def test_repeated_same_second_backups_do_not_collide(self):
        with temp_vault() as (vault, root):
            vault.write_note("todo.md", "one\n")
            vault.write_note("todo.md", "two\n")
            vault.write_note("todo.md", "three\n")
            backups = find_backups(root)
            self.assertEqual(len(backups), 2)
            by_name = {
                os.path.basename(full): read_file_bytes(full).decode("utf-8")
                for _rel, full in backups
            }
            self.assertEqual(by_name.get("todo.md"), "one\n")
            self.assertEqual(by_name.get("todo.md.1"), "two\n")


if __name__ == "__main__":
    unittest.main()
