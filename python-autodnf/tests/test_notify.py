import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from autodnf_py import notify


class NotificationTests(unittest.TestCase):
    def test_standalone_maintenance_notifies_once_and_preserves_status(self):
        for status in (0, 1, 130, 143):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copy(Path(__file__).resolve().parents[1] / "maintenance.sh", root)
                executable = root / ".venv/bin/python"
                executable.parent.mkdir(parents=True)
                executable.write_text(
                    '#!/bin/bash\n'
                    'echo "$*" >> calls\n'
                    'case "$*" in\n'
                    '  *autodnf_py.notify*) exit 1;;\n'
                    f'  *) exit {status};;\n'
                    'esac\n'
                )
                executable.chmod(0o700)
                result = subprocess.run(["bash", str(root / "maintenance.sh")], capture_output=True, text=True)
                self.assertEqual(result.returncode, status)
                calls = (root / "calls").read_text().splitlines()
                self.assertEqual(len(calls), 2)
                self.assertIn("maintenance --execute --debug", calls[0])
                self.assertIn(f"autodnf_py.notify --exit-code {status}", calls[1])
                self.assertIn("--phase standalone", calls[1])

    @patch.dict(os.environ, {"NTFY_TOPIC": "test-topic", "NTFY_TOKEN": "test-token"})
    def test_authenticated_publish(self):
        with patch.object(notify, "CONFIG", Path("/nonexistent-ntfy-config")), patch.object(notify, "urlopen") as publish:
            self.assertTrue(notify.send_notification("AutoDNF failed", "Failed during battle", True))
            request = publish.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
            self.assertEqual(request.data, b"Failed during battle")
            self.assertEqual(publish.call_args.kwargs["timeout"], 10)

    @patch.dict(os.environ, {"NTFY_TOPIC": "test-topic"})
    def test_permission_denial_is_reported_without_crashing(self):
        with patch.object(notify, "CONFIG", Path("/nonexistent-ntfy-config")), patch.object(notify, "urlopen", side_effect=HTTPError("url", 403, "Forbidden", {}, None)):
            self.assertFalse(notify.send_notification("test", "test"))

    def test_wrapper_notifies_and_preserves_exit_status(self):
        for battle_status, maintenance_status, expected_phase in (
            (0, 0, "complete"), (1, 0, "battle"), (0, 2, "maintenance"), (130, 0, "battle")
        ):
            with self.subTest(battle_status=battle_status, maintenance_status=maintenance_status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                shutil.copy(Path(__file__).resolve().parents[1] / "run-all.sh", root)
                executable = root / ".venv/bin/python"
                executable.parent.mkdir(parents=True)
                executable.write_text(
                    '#!/bin/bash\n'
                    'echo "$*" >> calls\n'
                    'case "$*" in\n'
                    '  *autodnf_py.notify*) exit 1;;\n'
                    f'  *maintenance*) exit {maintenance_status};;\n'
                    f'  *) exit {battle_status};;\n'
                    'esac\n'
                )
                executable.chmod(0o700)
                result = subprocess.run(["bash", str(root / "run-all.sh")], capture_output=True, text=True)
                self.assertEqual(result.returncode, battle_status or maintenance_status)
                calls = (root / "calls").read_text().splitlines()
                self.assertEqual(sum("autodnf_py.notify" in line for line in calls), 1)
                self.assertIn(f"--phase {expected_phase}", calls[-1])
                if battle_status:
                    self.assertFalse(any("maintenance --execute" in line for line in calls))
