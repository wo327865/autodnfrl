import unittest
from unittest.mock import Mock, patch

from autodnf_py.macos import TextBox
from autodnf_py.workflow import AutoDNF


class MailboxBackTests(unittest.TestCase):
    def make_flow(self):
        flow = AutoDNF.__new__(AutoDNF)
        flow.debug = False
        flow.client = Mock()
        flow.wait_for = Mock(return_value=(object(), []))
        flow.client.ocr.return_value = [
            TextBox("邮箱", 0.05, 0.9, 0.05, 0.03),
            TextBox("角色邮件", 0.05, 0.8, 0.1, 0.03),
        ]
        return flow

    @patch("autodnf_py.workflow.time.sleep")
    def test_unchanged_mailbox_tries_eight_times(self, sleep):
        flow = self.make_flow()
        with self.assertRaises(TimeoutError):
            flow.return_to_town_from_page("邮箱", "mailbox")
        self.assertEqual(flow.client.click.call_count, 8)
        self.assertEqual(sleep.call_count, 8)
        self.assertTrue(all(call.args == (1.0,) for call in sleep.call_args_list))

    @patch("autodnf_py.workflow.time.sleep")
    def test_stops_after_successful_second_click(self, sleep):
        flow = self.make_flow()
        mailbox = flow.client.ocr.return_value
        town = [TextBox("邮箱", 0.3, 0.1, 0.05, 0.03)]
        flow.client.ocr.side_effect = [mailbox, mailbox, town]
        flow.return_to_town_from_page("邮箱", "mailbox")
        self.assertEqual(flow.client.click.call_count, 2)
        self.assertEqual(sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
