#!/usr/bin/env python3
"""
End-to-end smoke test for the notes-vault MCP server.

Launches the server as a subprocess, speaks the MCP protocol over stdio
(newline-delimited JSON), and exercises every tool plus the safety rails
against a throwaway copy of the sample vault.

Run:
    python3 smoke_test.py

Exit code 0 means every check passed. Python 3 standard library only.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_SCRIPT = os.path.join(REPO_DIR, "notes_mcp_server.py")
SAMPLE_VAULT = os.path.join(REPO_DIR, "sample_vault")

TIMEOUT_SECONDS = 20


class SmokeFailure(Exception):
    pass


class McpClient:
    """A tiny MCP client: newline-delimited JSON over the server's stdio."""

    def __init__(self, server_argv):
        self.proc = subprocess.Popen(
            server_argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.next_id = 1

    def request(self, method, params=None):
        call_id = self.next_id
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": call_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        while True:
            line = self.proc.stdout.readline()
            if not line:
                stderr_tail = self.proc.stderr.read().decode("utf-8", "replace")[-2000:]
                raise SmokeFailure(
                    "the server closed stdout before answering call "
                    f"{call_id} ({method}). Server stderr ended with:\n{stderr_tail}"
                )
            try:
                reply = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise SmokeFailure(f"bad JSON line from server: {line!r} ({exc})")
            if reply.get("id") == call_id:
                return reply

    def notify(self, method, params=None):
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)

    def send(self, payload):
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
        self.proc.stdin.flush()

    def call_tool(self, tool, arguments):
        return self.request(
            "tools/call", {"name": tool, "arguments": arguments}
        )

    def tool_payload(self, tool, arguments):
        response = self.call_tool(tool, arguments)
        if "error" in response:
            raise SmokeFailure(f"{tool} got a protocol error: {response['error']}")
        content = response.get("result", {}).get("content")
        if not content or content[0].get("type") != "text":
            raise SmokeFailure(f"{tool} returned no text content: {response}")
        return json.loads(content[0]["text"])

    def tool_error(self, tool, arguments):
        response = self.call_tool(tool, arguments)
        result = response.get("result")
        if result is None or not result.get("isError"):
            raise SmokeFailure(
                f"{tool} with {arguments} should have failed, got: {response}"
            )
        return result["content"][0]["text"]

    def stop(self):
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=TIMEOUT_SECONDS)
        except Exception:
            self.proc.kill()
            self.proc.wait(timeout=TIMEOUT_SECONDS)


def check(label, condition, detail=""):
    if not condition:
        raise SmokeFailure(f"{label} failed. {detail}")
    print(f"  PASS {label}")


def main():
    checks_passed = 0

    workdir = tempfile.mkdtemp(prefix="notes-vault-smoke-")
    vault = os.path.join(workdir, "vault")
    shutil.copytree(SAMPLE_VAULT, vault)
    print("Smoke test: launching notes_mcp_server.py on a throwaway vault copy")

    client = McpClient([sys.executable, SERVER_SCRIPT, "--vault", vault])
    try:
        response = client.request("initialize", {"protocolVersion": "2024-11-05"})
        check(
            "initialize handshake",
            response.get("result", {}).get("serverInfo", {}).get("name") == "notes-vault-mcp"
            and response.get("result", {}).get("protocolVersion") == "2024-11-05",
            f"got {response}",
        )
        checks_passed += 1

        client.notify("notifications/initialized")

        response = client.request("tools/list")
        names = sorted(tool["name"] for tool in response["result"]["tools"])
        check(
            "tools/list shows the five tools",
            names == ["append_note", "list_notes", "read_note", "search_notes", "write_note"],
            f"got {names}",
        )
        checks_passed += 1

        payload = client.tool_payload("list_notes", {})
        check(
            "list_notes sees the sample notes",
            payload["count"] == 5 and "getting-started.md" in [n["path"] for n in payload["notes"]],
            f"got {payload}",
        )
        checks_passed += 1

        payload = client.tool_payload("read_note", {"path": "getting-started.md"})
        check("read_note returns note text", "MCP" in payload["content"], f"got {payload}")
        checks_passed += 1

        north = "smoke-test.md"
        payload = client.tool_payload(
            "write_note", {"path": north, "content": "# Smoke\n\nround trip\n"}
        )
        check("write_note creates a fresh note", payload["created"] is True, f"got {payload}")
        checks_passed += 1

        payload = client.tool_payload("read_note", {"path": north})
        check(
            "read_note round-trips the new note",
            payload["content"] == "# Smoke\n\nround trip\n",
            f"got {payload}",
        )
        checks_passed += 1

        payload = client.tool_payload("append_note", {"path": north, "content": "appended\n"})
        check("append_note appends", payload["bytes_appended"] == 9, f"got {payload}")
        checks_passed += 1

        payload = client.tool_payload(
            "write_note", {"path": north, "content": "# Smoke\n\nversion two\n"}
        )
        backup = payload.get("backup")
        check(
            "overwriting a note saves a backup under .backups/",
            isinstance(backup, str) and backup.startswith(".backups/"),
            f"got {payload}",
        )
        checks_passed += 1

        backup_full = os.path.join(vault, backup)
        with open(backup_full, "r", encoding="utf-8") as handle:
            check(
                "the backup holds the previous version",
                handle.read() == "# Smoke\n\nround trip\nappended\n",
                f"got {backup_full}",
            )
        checks_passed += 1

        payload = client.tool_payload("search_notes", {"query": "BACKUP"})
        check(
            "search_notes matches case-insensitively",
            payload["count"] >= 1,
            f"got {payload}",
        )
        checks_passed += 1

        error_text = client.tool_error("read_note", {"path": "../escaped.md"})
        check(
            "path traversal is blocked",
            "not allowed in paths" in error_text,
            error_text,
        )
        checks_passed += 1

        error_text = client.tool_error("write_note", {"path": "/etc/passwd", "content": "no"})
        check(
            "absolute paths are blocked",
            "absolute" in error_text,
            error_text,
        )
        checks_passed += 1

        error_text = client.tool_error("read_note", {"path": ".backups/" + os.path.basename(backup_full)})
        check(
            "hidden folders (including .backups) stay sealed",
            "Hidden items" in error_text,
            error_text,
        )
        checks_passed += 1

        error_text = client.tool_error("read_note", {"path": "readme.txt"})
        check(
            "non-Markdown files are refused",
            "Markdown" in error_text,
            error_text,
        )
        checks_passed += 1

    finally:
        client.stop()
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nSMOKE TEST PASSED ({checks_passed} checks green)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SmokeFailure as failure:
        print(f"\nSMOKE TEST FAILED: {failure}", file=sys.stderr)
        sys.exit(1)
