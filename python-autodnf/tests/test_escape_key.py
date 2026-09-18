import unittest
from unittest.mock import Mock, call, patch

from autodnf_py.macos import MacClient, Window


class EscapeKeyTests(unittest.TestCase):
    @patch("autodnf_py.macos.time.sleep")
    @patch("autodnf_py.macos.CGEventPostToPid")
    @patch("autodnf_py.macos.CGEventCreateKeyboardEvent")
    @patch("autodnf_py.macos.NSRunningApplication")
    def test_escape_is_posted_directly_to_game_pid(
        self,
        running_application,
        create_keyboard_event,
        post_to_pid,
        sleep,
    ):
        client = MacClient(execute=True)
        client.find_window = Mock(
            return_value=Window(7, 4321, "PlayCover", "DNF", 0, 0, 1000, 600)
        )
        app = Mock()
        running_application.runningApplicationWithProcessIdentifier_.return_value = app
        create_keyboard_event.side_effect = ["escape-down", "escape-up"]

        client.press_escape()

        app.activateWithOptions_.assert_called_once_with(1)
        self.assertEqual(
            create_keyboard_event.call_args_list,
            [call(None, 53, True), call(None, 53, False)],
        )
        self.assertEqual(
            post_to_pid.call_args_list,
            [call(4321, "escape-down"), call(4321, "escape-up")],
        )
        self.assertEqual(sleep.call_args_list, [call(0.25), call(0.18)])


if __name__ == "__main__":
    unittest.main()
