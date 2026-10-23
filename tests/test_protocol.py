"""Unit tests for the MCP JSON-RPC protocol layer (no subprocess needed)."""

import json
import os
import unittest

try:
    from tests.helpers import temp_vault, find_backups, read_file_bytes, nms
except ImportError:
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tests.helpers import temp_vault, find_backups, read_file_bytes, nms

from notes_mcp_server import Server, _rpc_error, validate_arguments


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        stack = temp_vault()
        vault, root = stack.__enter__()
        self.addCleanup(stack.__exit__, None, None, None)
        self.root = root
        self.server = Server(vault)

    # -- initialize / ping -------------------------------------------------

    def test_initialize_echoes_a_supported_protocol_version(self):
        response = self.server.process(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "clientInfo": {"name": "t"}},
            }
        )
        self.assertEqual(response["id"], 1)
        self.assertEqual(response["result"]["protocolVersion"], "2024-11-05")
        self.assertIn("tools", response["result"]["capabilities"])
        self.assertEqual(response["result"]["serverInfo"]["name"], "notes-vault-mcp")
        self.assertIn("instructions", response["result"])

    def test_initialize_with_future_version_answers_with_latest_supported(self):
        response = self.server.process(
            {"jsonrpc": "2.0", "id": "abc", "method": "initialize", "params": {"protocolVersion": "9999-99-99"}}
        )
        self.assertEqual(response["id"], "abc")
        self.assertEqual(response["result"]["protocolVersion"], "2025-06-18")

    def test_ping_answers_with_empty_result(self):
        response = self.server.process({"jsonrpc": "2.0", "id": 7, "method": "ping"})
        self.assertEqual(response["result"], {})

    # -- tools/list --------------------------------------------------------

    def test_tools_list_offers_exactly_the_five_tools(self):
        response = self.server.process({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(
            sorted(names),
            ["append_note", "list_notes", "read_note", "search_notes", "write_note"],
        )
        for tool in response["result"]["tools"]:
            self.assertIn("description", tool)
            self.assertEqual(tool["inputSchema"]["type"], "object")

    # -- tools/call round-trips --------------------------------------------

    def _call(self, tool, arguments, call_id=10):
        return self.server.process(
            {
                "jsonrpc": "2.0",
                "id": call_id,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )

    @staticmethod
    def _payload(response):
        return json.loads(response["result"]["content"][0]["text"])

    def test_write_then_read_round_trip_through_the_protocol(self):
        response = self._call("write_note", {"path": "todo.md", "content": "# Todo\n"})
        self.assertFalse(response["result"]["isError"])
        self.assertTrue(self._payload(response)["created"])

        read_response = self._call("read_note", {"path": "todo.md"})
        self.assertFalse(read_response["result"]["isError"])
        self.assertEqual(json.loads(read_response["result"]["content"][0]["text"])["content"], "# Todo\n")

    def test_search_via_protocol_is_case_insensitive(self):
        response = self._call("search_notes", {"query": "BACKUP"})
        payload = self._payload(response)
        self.assertGreaterEqual(payload["count"], 1)
        self.assertEqual(payload["matches"][0]["path"], "getting-started.md")

    def test_list_via_protocol_counts_seed_notes(self):
        payload = self._payload(self._call("list_notes", {}))
        self.assertEqual(payload["count"], 3)
        self.assertEqual(
            [note["path"] for note in payload["notes"]],
            ["getting-started.md", "ideas/chess.md", "journal/2026-10-22.md"],
        )

    def test_unknown_tool_is_error_32602(self):
        response = self._call("delete_note", {"path": "x.md"})
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("Unknown tool", response["error"]["message"])

    def test_missing_required_argument_is_error_32602(self):
        response = self._call("write_note", {"path": "todo.md"})
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("required", response["error"]["message"])

    def test_wrong_type_argument_is_error_32602(self):
        response = self._call("list_notes", {"folder": 42})
        self.assertEqual(response["error"]["code"], -32602)

    def test_out_of_range_limit_is_error_32602(self):
        response = self._call("search_notes", {"query": "x", "limit": 99999})
        self.assertEqual(response["error"]["code"], -32602)

    def test_unknown_argument_is_error_32602(self):
        response = self._call("read_note", {"path": "todo.md", "evil": True})
        self.assertEqual(response["error"]["code"], -32602)
        self.assertIn("Unknown argument", response["error"]["message"])

    def test_runtime_safety_rule_becomes_isError_result_not_protocol_error(self):
        response = self._call("read_note", {"path": "../escape.md"})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("Error:", response["result"]["content"][0]["text"])
        self.assertNotIn("result", response.get("error", {}))

    def test_missing_note_reports_isError_with_a_friendly_message(self):
        response = self._call("read_note", {"path": "ghost.md"})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("Note not found", response["result"]["content"][0]["text"])

    def test_safety_rail_keeps_backups_and_traversal_out(self):
        self._call("write_note", {"path": "todo.md", "content": "v1\n"})
        self._call("write_note", {"path": "todo.md", "content": "v2\n"})
        rel, full = find_backups(self.root)[0]
        self.assertEqual(read_file_bytes(full).decode("utf-8"), "v1\n")
        blocked = self._call("read_note", {"path": ".backups/" + rel})
        self.assertTrue(blocked["result"]["isError"])
        self.assertIn("Hidden items", blocked["result"]["content"][0]["text"])

    # -- request hygiene ---------------------------------------------------

    def test_unknown_method_is_error_32601(self):
        response = self.server.process({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})
        self.assertEqual(response["error"]["code"], -32601)
        self.assertIn("resources/list", response["error"]["message"])

    def test_notifications_get_no_response(self):
        self.assertIsNone(
            self.server.process({"jsonrpc": "2.0", "method": "notifications/initialized"})
        )
        self.assertIsNone(self.server.process({"jsonrpc": "2.0", "method": "some/future"}))
        self.assertIsNone(self.server.process({"jsonrpc": "2.0", "method": "tools/list"}))

    def test_missing_jsonrpc_field_is_invalid_request(self):
        response = self.server.process({"id": 4, "method": "ping"})
        self.assertEqual(response["error"]["code"], -32600)

    def test_non_object_message_is_invalid_request(self):
        response = self.server.process(["nope"])
        self.assertEqual(response["error"]["code"], -32600)
        self.assertIsNone(response["id"])


class ValidateArgumentsTests(unittest.TestCase):
    def test_none_arguments_become_empty_object(self):
        schema = {"properties": {"folder": {"type": "string"}}, "required": []}
        self.assertEqual(validate_arguments(schema, None), {})

    def test_bool_rejected_for_integer_fields(self):
        schema = {"properties": {"limit": {"type": "integer", "minimum": 1}}, "required": []}
        with self.assertRaises(Exception):
            validate_arguments(schema, {"limit": True})


class RpErrorShapeTests(unittest.TestCase):
    def test_rpc_error_shape(self):
        error = _rpc_error(None, -32700, "bad")
        self.assertEqual(error["jsonrpc"], "2.0")
        self.assertIsNone(error["id"])
        self.assertEqual(error["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
