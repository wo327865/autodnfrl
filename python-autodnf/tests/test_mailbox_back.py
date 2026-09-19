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

    @patch("autodnf_py.workflow.time.sleep")
    def test_delayed_login_ad_is_closed_without_consuming_mailbox_attempt(self, sleep):
        flow = AutoDNF.__new__(AutoDNF)
        flow.debug = False
        flow.in_dungeon = False
        flow.special_signin_dismiss_attempts = 0
        flow.client = Mock()
        flow.client.execute = True
        window = object()
        town = [
            TextBox("委托", 0.85, 0.15, 0.05, 0.03),
            TextBox("邮箱", 0.30, 0.10, 0.05, 0.03),
        ]
        delayed_ad = [
            TextBox("3分钟直升15万", 0.35, 0.75, 0.30, 0.08),
            TextBox("全能黄金胶囊", 0.65, 0.55, 0.20, 0.04),
            TextBox("本角色已使用", 0.40, 0.30, 0.20, 0.04),
        ]
        mailbox = [TextBox("角色邮件", 0.05, 0.80, 0.10, 0.03)]
        flow.wait_for = Mock(return_value=(window, town))
        flow.client.find_window.return_value = window
        flow.client.ocr.side_effect = [delayed_ad, mailbox]

        result_window, result_boxes = flow.open_mailbox_with_fast_retries()

        self.assertIs(result_window, window)
        self.assertIs(result_boxes, mailbox)
        labels = [call.args[2] for call in flow.client.click.call_args_list]
        self.assertEqual(
            labels,
            [
                "邮箱 (1/8)",
                "close level-boost advertisement",
                "邮箱 (1/8)",
            ],
        )


if __name__ == "__main__":
    unittest.main()
