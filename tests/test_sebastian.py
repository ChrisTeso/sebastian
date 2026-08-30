from __future__ import annotations

import datetime as dt
import contextlib
import json
import plistlib
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sebastian.constants import SIGNATURE  # noqa: E402
import sebastian.cli as cli_module  # noqa: E402
from sebastian.generator import generate_response  # noqa: E402
from sebastian.keychain import KeychainError, get_api_key  # noqa: E402
from sebastian.messages import (  # noqa: E402
    connect_readonly,
    matching_outgoing_rowid,
    message_text_hash,
    messages_after,
    outgoing_delivery_state,
    participants,
    previous_messages,
)
from sebastian.policy import (  # noqa: E402
    contains_trigger,
    finalize_response,
    is_loop_response,
    allowlisted,
    should_trigger,
    strip_trigger,
)
from sebastian.sender import automation_probe, send_to_chat  # noqa: E402
from sebastian.service import SebastianService, verify_trigger  # noqa: E402
from sebastian.state import StateStore  # noqa: E402


def build_messages_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY, guid TEXT, date INTEGER, is_from_me INTEGER,
            handle_id INTEGER, text TEXT, attributedBody BLOB,
            cache_has_attachments INTEGER DEFAULT 0, is_system_message INTEGER DEFAULT 0,
            associated_message_type INTEGER DEFAULT 0, is_sent INTEGER DEFAULT 1,
            error INTEGER DEFAULT 0
        );
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT);
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        INSERT INTO chat VALUES (1, 'chat-one'), (2, 'chat-two');
        INSERT INTO handle VALUES (1, 'person-one'), (2, 'person-two');
        INSERT INTO chat_handle_join VALUES (1, 1), (2, 2);
        INSERT INTO message(ROWID,guid,date,is_from_me,handle_id,text) VALUES
          (1, 'm1', 1, 0, 1, 'one private canary'),
          (2, 'm2', 2, 0, 2, 'other-chat canary'),
          (3, 'm3', 3, 1, 1, '@sebastian outgoing'),
          (4, 'm4', 4, 0, 1, 'hey, @Sebastian!'),
          (5, 'm5', 5, 1, 1, 'answer\n\n— Sebastian, Chris’s AI assistant');
        INSERT INTO message(ROWID,guid,date,is_from_me,handle_id,text,associated_message_type)
          VALUES (6, 'm6', 6, 0, 1, 'reacted to @sebastian', 2000);
        INSERT INTO message(ROWID,guid,date,is_from_me,handle_id,text,is_sent,error)
          VALUES (7, 'm7', 7, 1, 1, 'failed answer', 0, 5);
        INSERT INTO chat_message_join VALUES
          (1, 1), (2, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7);
        """
    )
    conn.commit()
    conn.close()


class KeychainBoundaryTest(unittest.TestCase):
    def test_key_is_accepted_only_from_native_launcher_environment(self) -> None:
        with patch.dict("os.environ", {"SEBASTIAN_OPENAI_API_KEY": "test-key"}, clear=False):
            self.assertEqual(get_api_key(), "test-key")
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(KeychainError):
                get_api_key()


class AutomationProbeTest(unittest.TestCase):
    def test_probe_counts_enabled_accounts_without_filtered_reference(self) -> None:
        completed = types.SimpleNamespace(returncode=0, stdout="2\n")
        with patch("sebastian.sender.subprocess.run", return_value=completed) as run:
            self.assertEqual(automation_probe(), 2)
        script = run.call_args.kwargs["input"]
        self.assertIn("repeat with accountItem in accounts", script)
        self.assertNotIn("whose enabled", script)


class TriggerPolicyTest(unittest.TestCase):
    def test_case_and_punctuation_variants_trigger(self) -> None:
        for text in ("@Sebastian help", "hey, @sebastian!", "(@SEBASTIAN): help?"):
            self.assertTrue(contains_trigger(text), text)

    def test_email_like_text_does_not_trigger(self) -> None:
        self.assertFalse(contains_trigger("mail user@sebastian.com"))

    def test_incoming_and_outgoing_mentions_trigger_but_signed_responses_do_not(self) -> None:
        self.assertTrue(should_trigger(text="@sebastian help", is_from_me=True))
        self.assertTrue(should_trigger(text="@sebastian help", is_from_me=False))
        signed = f"@sebastian quoted\n\n{SIGNATURE}"
        self.assertTrue(is_loop_response(signed))
        self.assertFalse(should_trigger(text=signed, is_from_me=False))
        self.assertFalse(should_trigger(text=signed, is_from_me=True))

    def test_trigger_is_removed_cleanly(self) -> None:
        self.assertEqual(strip_trigger("Hey, @Sebastian! What time?"), "Hey! What time?")

    def test_participant_allowlist_applies_to_sender_not_any_group_member(self) -> None:
        config = {
            "allowlist": {
                "enabled": True,
                "conversation_guids": [],
                "participants": ["allowed-person"],
            }
        }
        self.assertTrue(allowlisted(config, "group-chat", "allowed-person"))
        self.assertFalse(allowlisted(config, "group-chat", "different-person"))
        self.assertTrue(
            allowlisted(config, "group-chat", "different-person", is_from_me=True)
        )
        config["allowlist"]["conversation_guids"] = ["group-chat"]
        self.assertTrue(allowlisted(config, "group-chat", "different-person"))


class SignatureTest(unittest.TestCase):
    def test_signature_is_appended_exactly_once(self) -> None:
        value = finalize_response(f"{SIGNATURE}\nDone.\n\n{SIGNATURE}", 300)
        self.assertEqual(value.count(SIGNATURE), 1)
        self.assertTrue(value.endswith(SIGNATURE))

    def test_response_limit_includes_signature(self) -> None:
        value = finalize_response("x" * 500, 100)
        self.assertLessEqual(len(value), 100)
        self.assertTrue(value.endswith(SIGNATURE))

    def test_markdown_is_rendered_as_messages_friendly_plain_text(self) -> None:
        value = finalize_response(
            "# Result\n**Bold** and *italic* with `code`.\n"
            "[Source](https://example.com/path_with_underscores)",
            300,
        )
        self.assertIn("Result\nBold and italic with code.", value)
        self.assertIn("Source (https://example.com/path_with_underscores)", value)
        self.assertNotIn("**", value)
        self.assertNotIn("[Source]", value)


class MessagesIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "chat.db"
        build_messages_db(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_context_never_crosses_conversations(self) -> None:
        with connect_readonly(self.path) as conn:
            history = previous_messages(conn, 1, 4, 20)
        bodies = [message.text for message in history]
        self.assertIn("one private canary", bodies)
        self.assertNotIn("other-chat canary", bodies)
        self.assertNotIn("hey, @Sebastian!", bodies)

    def test_incoming_and_outgoing_mentions_both_trigger(self) -> None:
        with connect_readonly(self.path) as conn:
            rows = messages_after(conn, 0)
        triggers = [m.rowid for m in rows if should_trigger(text=m.text, is_from_me=m.is_from_me)]
        self.assertEqual(triggers, [3, 4])

    def test_reaction_quoting_tag_is_not_a_trigger_or_context(self) -> None:
        with connect_readonly(self.path) as conn:
            rows = messages_after(conn, 5)
            history = previous_messages(conn, 1, 8, 20)
        self.assertEqual([message.rowid for message in rows], [7])
        self.assertNotIn(6, [message.rowid for message in history])

    def test_participants_are_conversation_scoped(self) -> None:
        with connect_readonly(self.path) as conn:
            self.assertEqual(participants(conn, 1), ["person-one"])
            self.assertEqual(participants(conn, 2), ["person-two"])

    def test_reconciliation_matches_only_same_chat_outgoing_hash(self) -> None:
        response = f"answer\n\n{SIGNATURE}"
        digest = message_text_hash(response)
        with connect_readonly(self.path) as conn:
            rowid = matching_outgoing_rowid(
                conn,
                chat_id=1,
                response_hash=digest,
                since=dt.datetime(2001, 1, 1, tzinfo=dt.UTC),
            )
            absent = matching_outgoing_rowid(
                conn,
                chat_id=2,
                response_hash=digest,
                since=dt.datetime(2001, 1, 1, tzinfo=dt.UTC),
            )
        self.assertEqual(rowid, 5)
        self.assertIsNone(absent)

    def test_failed_outgoing_row_is_not_reconciled_as_sent(self) -> None:
        digest = message_text_hash("failed answer")
        with connect_readonly(self.path) as conn:
            status, rowid = outgoing_delivery_state(
                conn,
                chat_id=1,
                response_hash=digest,
                since=dt.datetime(2001, 1, 1, tzinfo=dt.UTC),
            )
            successful = matching_outgoing_rowid(
                conn,
                chat_id=1,
                response_hash=digest,
                since=dt.datetime(2001, 1, 1, tzinfo=dt.UTC),
            )
        self.assertEqual((status, rowid), ("failed", 7))
        self.assertIsNone(successful)


class StateReliabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.now = dt.datetime.now(dt.UTC).replace(microsecond=0)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def discover(self, state: StateStore, rowid: int, chat_id: int = 1, limit: int = 3) -> str:
        return state.discover_trigger(
            chat_id=chat_id,
            message_rowid=rowid,
            per_conversation_limit=limit,
            now=self.now,
        )

    def test_deduplication_survives_restart(self) -> None:
        state = StateStore(self.path)
        self.assertEqual(self.discover(state, 10), "pending")
        trigger = state.due_triggers(self.now)[0]
        state.mark_sending(trigger.id, "response-hash", self.now)
        state.mark_sent(trigger.id, 11)
        state.close()

        reopened = StateStore(self.path)
        self.assertEqual(self.discover(reopened, 10), "duplicate")
        self.assertEqual(reopened.due_triggers(self.now), [])
        self.assertEqual(reopened.status_counts(), {"sent": 1})
        persisted = reopened.trigger_by_id(trigger.id)
        self.assertIsNone(persisted.response_hash)
        self.assertIsNone(persisted.send_started_at)
        reopened.close()

    def test_per_conversation_rate_limit_is_atomic(self) -> None:
        state = StateStore(self.path)
        self.assertEqual(self.discover(state, 1, limit=2), "pending")
        self.assertEqual(self.discover(state, 2, limit=2), "pending")
        self.assertEqual(self.discover(state, 3, limit=2), "rate_limited")
        self.assertEqual(len(state.due_triggers(self.now)), 2)
        state.close()

    def test_global_daily_api_limit(self) -> None:
        state = StateStore(self.path)
        self.discover(state, 1)
        self.discover(state, 2)
        first, second = state.due_triggers(self.now)
        self.assertTrue(state.reserve_api_call(first.id, 1, self.now))
        self.assertFalse(state.reserve_api_call(second.id, 1, self.now))
        self.assertEqual(state.api_calls_today(), 1)
        self.assertEqual(state.status_counts()["daily_limited"], 1)
        state.close()

    def test_interrupted_generation_requeues_without_response_text(self) -> None:
        state = StateStore(self.path)
        self.discover(state, 1)
        trigger = state.due_triggers(self.now)[0]
        state.reserve_api_call(trigger.id, 10, self.now)
        self.assertEqual(state.recover_interrupted_generation(), 1)
        columns = [row[1] for row in state.conn.execute("PRAGMA table_info(triggers)")]
        self.assertNotIn("response_text", columns)
        state.close()

    def test_expired_completed_metadata_and_counters_are_pruned(self) -> None:
        state = StateStore(self.path)
        old = self.now - dt.timedelta(days=10)
        self.discover(state, 1)
        trigger = state.due_triggers(self.now)[0]
        state.conn.execute(
            "UPDATE triggers SET discovered_at=?, status='sent' WHERE id=?",
            ((old.isoformat().replace("+00:00", "Z")), trigger.id),
        )
        state.conn.execute(
            "UPDATE rate_events SET occurred_at=? WHERE trigger_id=?",
            ((old.isoformat().replace("+00:00", "Z")), trigger.id),
        )
        state.conn.commit()
        state.prune(7, self.now)
        self.assertIsNone(state.trigger_by_id(trigger.id))
        state.close()


class SenderPrivacyTest(unittest.TestCase):
    def test_message_body_is_not_put_in_process_arguments(self) -> None:
        secret = "private body with @sebastian and quotes \"here\""
        captured: dict[str, object] = {}

        class Result:
            returncode = 0

        def fake_run(args: list[str], **kwargs: object) -> Result:
            captured["args"] = args
            captured["input"] = kwargs["input"]
            return Result()

        with patch("sebastian.sender.subprocess.run", side_effect=fake_run):
            send_to_chat("chat-guid", secret)
        self.assertNotIn(secret, " ".join(captured["args"]))
        self.assertNotIn(secret, str(captured["input"]))
        self.assertNotIn("chat-guid", " ".join(captured["args"]))
        self.assertNotIn("chat-guid", str(captured["input"]))


class GeneratorBoundaryTest(unittest.TestCase):
    def test_responses_api_is_stateless_and_exposes_only_web_search(self) -> None:
        captured: dict[str, object] = {}

        class FakeResponses:
            def create(self, **kwargs: object) -> object:
                captured.update(kwargs)
                annotation = types.SimpleNamespace(
                    type="url_citation", title="Example Source", url="https://example.com/current"
                )
                content = types.SimpleNamespace(annotations=[annotation])
                return types.SimpleNamespace(
                    output_text="OK", output=[types.SimpleNamespace(content=[content])]
                )

        class FakeClient:
            def __init__(self, **kwargs: object) -> None:
                captured["client_kwargs"] = kwargs
                self.responses = FakeResponses()

        trigger = types.SimpleNamespace(
            sender="recipient-handle",
            has_attachments=False,
            is_from_me=True,
            timestamp="2026-08-29T00:00:00Z",
            text="@sebastian current news?",
        )
        history = [
            types.SimpleNamespace(
                sender="sender-one",
                has_attachments=False,
                is_from_me=False,
                timestamp="2026-08-28T00:00:00Z",
                text="same-chat history",
            )
        ]
        with (
            patch("openai.OpenAI", FakeClient),
            patch("sebastian.generator.get_api_key", return_value="test-key-not-real"),
        ):
            result = generate_response(
                config={
                    "model": "test-model",
                    "web_search": {"enabled": True, "search_context_size": "low"},
                },
                request="current news?",
                trigger=trigger,
                history=history,
                participants=["sender-one"],
            )
        self.assertIn("OK", result)
        self.assertIn("Example Source: https://example.com/current", result)
        self.assertIs(captured["store"], False)
        self.assertEqual(captured["client_kwargs"]["max_retries"], 0)
        self.assertEqual(captured["tools"], [{"type": "web_search", "search_context_size": "low"}])
        self.assertEqual(captured["reasoning"], {"effort": "medium"})
        self.assertIn("same-chat history", str(captured["input"]))
        self.assertIn("Trigger sender (untrusted data): Chris", str(captured["input"]))
        self.assertNotIn("Trigger sender (untrusted data): recipient-handle", str(captured["input"]))
        request_payload = {key: value for key, value in captured.items() if key != "client_kwargs"}
        self.assertNotIn("test-key-not-real", str(request_payload))


class ServicePrivacyTest(unittest.TestCase):
    def service_config(self, db_path: Path) -> dict:
        return {
            "enabled": True,
            "model": "test",
            "messages_db": str(db_path),
            "poll_seconds": 5,
            "max_response_chars": 300,
            "max_context_messages": 20,
            "max_triggers_per_conversation_per_minute": 3,
            "global_daily_api_call_limit": 10,
            "state_retention_days": 7,
            "retry": {"initial_seconds": 1, "maximum_seconds": 2, "send_reconcile_seconds": 1},
            "allowlist": {"enabled": False, "conversation_guids": [], "participants": []},
            "web_search": {"enabled": False},
        }

    def test_discovery_logs_and_state_do_not_retain_message_bodies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "chat.db"
            build_messages_db(db_path)
            config = self.service_config(db_path)
            with (
                patch.dict("os.environ", {"SEBASTIAN_HOME": str(root / "home")}),
                patch("sebastian.service.DEFAULT_LOG_DIR", root / "logs"),
            ):
                service = SebastianService(config)
                service.state.set_meta("last_seen_rowid", "0")
                service.discover()
                self.assertEqual(len(service.state.due_triggers()), 2)
                trigger_rows = service.state.conn.execute(
                    "SELECT chat_id, message_rowid, status FROM triggers"
                ).fetchall()
                service.close()
            combined = (root / "logs" / "service.log").read_text(encoding="utf-8")
            state_values = " ".join(str(value) for row in trigger_rows for value in row)
            for secret in ("one private canary", "other-chat canary", "@Sebastian"):
                self.assertNotIn(secret, combined)
                self.assertNotIn(secret, state_values)

    def test_outgoing_trigger_bypasses_incoming_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "chat.db"
            build_messages_db(db_path)
            config = self.service_config(db_path)
            config["allowlist"] = {
                "enabled": True,
                "conversation_guids": [],
                "participants": ["someone-else"],
            }
            with (
                patch.dict("os.environ", {"SEBASTIAN_HOME": str(root / "home")}),
                patch("sebastian.service.DEFAULT_LOG_DIR", root / "logs"),
            ):
                service = SebastianService(config)
                service.state.set_meta("last_seen_rowid", "0")
                service.discover()
                rowids = [trigger.message_rowid for trigger in service.state.due_triggers()]
                service.close()
            self.assertEqual(rowids, [3])

    def test_kill_switch_prevents_polling(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.service_config(root / "missing.db")
            home = root / "home"
            home.mkdir()
            (home / "DISABLED").touch()
            with (
                patch.dict("os.environ", {"SEBASTIAN_HOME": str(home)}),
                patch("sebastian.service.DEFAULT_LOG_DIR", root / "logs"),
                patch("sebastian.service.connect_readonly") as connect,
            ):
                service = SebastianService(config)
                service.run_once()
                service.close()
            connect.assert_not_called()

    def test_accepted_send_remains_pending_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "chat.db"
            build_messages_db(db_path)
            config = self.service_config(db_path)
            with (
                patch.dict("os.environ", {"SEBASTIAN_HOME": str(root / "home")}),
                patch("sebastian.service.DEFAULT_LOG_DIR", root / "logs"),
            ):
                service = SebastianService(config)
                service.state.discover_trigger(
                    chat_id=1,
                    message_rowid=4,
                    per_conversation_limit=3,
                )
                trigger = service.state.due_triggers()[0]
                with (
                    patch("sebastian.service.connect_readonly", return_value=contextlib.nullcontext(object())),
                    patch("sebastian.service.message_by_id") as fetch_message,
                    patch("sebastian.service.participants", return_value=["person-one"]),
                    patch("sebastian.service.previous_messages", return_value=[]),
                    patch("sebastian.service.chat_guid", return_value="chat-one"),
                    patch("sebastian.service.generate_response", return_value="A reply"),
                    patch("sebastian.service.send_to_chat") as send,
                ):
                    fetch_message.return_value = types.SimpleNamespace(
                        text="@sebastian help",
                        is_from_me=False,
                        sender="person-one",
                        has_attachments=False,
                    )
                    service.process_trigger(trigger)
                self.assertEqual(service.state.status_counts(), {"sending": 1})
                send.assert_called_once()
                service.close()

    def test_pending_trigger_rechecks_restrictive_allowlist_before_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "chat.db"
            build_messages_db(db_path)
            config = self.service_config(db_path)
            config["allowlist"] = {
                "enabled": True,
                "conversation_guids": [],
                "participants": ["someone-else"],
            }
            with (
                patch.dict("os.environ", {"SEBASTIAN_HOME": str(root / "home")}),
                patch("sebastian.service.DEFAULT_LOG_DIR", root / "logs"),
            ):
                service = SebastianService(config)
                service.state.discover_trigger(chat_id=1, message_rowid=4, per_conversation_limit=3)
                trigger = service.state.due_triggers()[0]
                with patch("sebastian.service.generate_response") as generate:
                    service.process_trigger(trigger)
                self.assertEqual(service.state.status_counts(), {"ignored": 1})
                generate.assert_not_called()
                service.close()

    def test_allowlist_change_during_generation_prevents_send(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "chat.db"
            build_messages_db(db_path)
            config = self.service_config(db_path)
            restricted = dict(config)
            restricted["allowlist"] = {
                "enabled": True,
                "conversation_guids": [],
                "participants": ["someone-else"],
            }
            with (
                patch.dict("os.environ", {"SEBASTIAN_HOME": str(root / "home")}),
                patch("sebastian.service.DEFAULT_LOG_DIR", root / "logs"),
            ):
                service = SebastianService(config)
                service.state.discover_trigger(chat_id=1, message_rowid=4, per_conversation_limit=3)
                trigger = service.state.due_triggers()[0]
                with (
                    patch("sebastian.service.generate_response", return_value="A reply"),
                    patch("sebastian.service.load_config", return_value=restricted),
                    patch("sebastian.service.send_to_chat") as send,
                ):
                    service.process_trigger(trigger)
                self.assertEqual(service.state.status_counts(), {"ignored": 1})
                send.assert_not_called()
                service.close()

    def test_installed_identity_trigger_verifier_checks_same_chat_signature(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            home = root / "home"
            home.mkdir()
            db_path = root / "chat.db"
            build_messages_db(db_path)
            (home / "config.json").write_text(
                json.dumps({"messages_db": str(db_path)}), encoding="utf-8"
            )
            with patch.dict("os.environ", {"SEBASTIAN_HOME": str(home)}):
                state = StateStore(home / "state.sqlite3")
                state.discover_trigger(chat_id=1, message_rowid=4, per_conversation_limit=3)
                trigger = state.due_triggers()[0]
                state.mark_sending(trigger.id, message_text_hash(f"answer\n\n{SIGNATURE}"))
                state.mark_sent(trigger.id, 5)
                state.close()
                self.assertEqual(verify_trigger(trigger.id), 0)

    def test_daily_pruning_runs_without_service_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = self.service_config(root / "unused.db")
            with (
                patch.dict("os.environ", {"SEBASTIAN_HOME": str(root / "home")}),
                patch("sebastian.service.DEFAULT_LOG_DIR", root / "logs"),
            ):
                service = SebastianService(config)
                old = dt.datetime.now(dt.UTC) - dt.timedelta(days=2)
                service.state.set_meta("last_prune_at", old.isoformat().replace("+00:00", "Z"))
                with patch.object(service.state, "prune") as prune:
                    service.prune_if_due()
                prune.assert_called_once()
                service.close()


class CliIdentityTest(unittest.TestCase):
    def test_failed_start_keeps_kill_switch_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            plist = root / "sebastian.plist"
            plist.touch()
            failed = types.SimpleNamespace(returncode=1, stdout="")
            with (
                patch("sebastian.cli.DEFAULT_PLIST", plist),
                patch("sebastian.cli.sebastian_home", return_value=root / "home"),
                patch("sebastian.cli.is_loaded", return_value=False),
                patch("sebastian.cli.launchctl", return_value=failed),
            ):
                self.assertEqual(cli_module.command_start(None), 1)
            self.assertTrue((root / "home" / "DISABLED").exists())

    def test_stop_waits_for_launchagent_to_disappear(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            succeeded = types.SimpleNamespace(returncode=0, stdout="")
            with (
                patch("sebastian.cli.sebastian_home", return_value=root / "home"),
                patch("sebastian.cli.is_loaded", side_effect=[True, True, False]),
                patch("sebastian.cli.launchctl", return_value=succeeded),
                patch("sebastian.cli.time.sleep"),
            ):
                self.assertEqual(cli_module.command_stop(None), 0)
            self.assertTrue((root / "home" / "DISABLED").exists())

    def test_installed_probe_uses_one_shot_launchagent_and_cleans_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app = root / "Sebastian.app"
            launcher = app / "Contents" / "MacOS" / "Sebastian"
            launcher.parent.mkdir(parents=True)
            launcher.touch()
            calls: list[tuple[str, ...]] = []

            def fake_launchctl(*args: str) -> object:
                calls.append(args)
                if args[0] == "bootstrap":
                    plist = Path(args[-1])
                    payload = plistlib.loads(plist.read_bytes())
                    Path(payload["EnvironmentVariables"]["SEBASTIAN_DIAGNOSTIC_RESULT"]).write_text(
                        "0\n", encoding="utf-8"
                    )
                return types.SimpleNamespace(returncode=0, stdout="")

            with (
                patch("sebastian.cli.DEFAULT_APP", app),
                patch("sebastian.cli.sebastian_home", return_value=root / "home"),
                patch("sebastian.cli.launchctl", side_effect=fake_launchctl),
            ):
                self.assertTrue(cli_module.installed_probe("doctor-database", timeout=1))
            self.assertEqual(calls[0][0], "bootstrap")
            self.assertEqual(calls[-1][0], "bootout")
            self.assertEqual(list((root / "home").glob("diagnostic-*")), [])


if __name__ == "__main__":
    unittest.main()
