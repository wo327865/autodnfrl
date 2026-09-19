import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from autodnf_py.macos import Window
from autodnf_py.template_matcher import TemplateMatch
from autodnf_py.workflow import AutoDNF


def match(name, score=0.99):
    return TemplateMatch(
        name=name,
        score=score,
        region=(0.9, 0.9, 0.98, 0.98),
        click_point_top_left=(0.95, 0.95),
        source=Path(f"{name}.png"),
    )


class DismantleOpenRetryTests(unittest.TestCase):
    def make_flow(self):
        flow = AutoDNF.__new__(AutoDNF)
        flow.client = Mock()
        flow.template_matcher = Mock()
        flow.click_template = Mock()
        window = Window(1, 2, "PlayCover", "DNF", 0, 0, 1000, 600)
        flow.client.find_window.return_value = window
        return flow, window

    @patch("autodnf_py.workflow.time.sleep")
    def test_retries_after_one_second_while_open_button_remains(self, sleep):
        flow, window = self.make_flow()
        frames = [b"first", b"second"]
        flow.client.capture_png_bytes.side_effect = frames

        def detect(frame, name):
            if frame == b"first" and name == "dismantle_open":
                return match("dismantle_open", 0.98)
            if frame == b"second" and name == "dismantle_ready":
                return match("dismantle_ready", 0.97)
            return None

        flow.template_matcher.match.side_effect = detect
        result = flow.open_dismantle_panel_with_retries(
            window,
            match("dismantle_open"),
        )

        self.assertEqual(result[1].name, "dismantle_ready")
        self.assertEqual(flow.click_template.call_count, 2)
        self.assertEqual(
            [item.args[2] for item in flow.click_template.call_args_list],
            ["open dismantle panel (1/8)", "open dismantle panel (2/8)"],
        )
        self.assertEqual(sleep.call_args_list, [call(1.0), call(1.0)])

    @patch("autodnf_py.workflow.time.sleep")
    def test_stops_after_eight_unchanged_clicks(self, sleep):
        flow, window = self.make_flow()
        flow.client.capture_png_bytes.return_value = b"unchanged"
        flow.template_matcher.match.side_effect = (
            lambda frame, name: match("dismantle_open")
            if name == "dismantle_open"
            else None
        )
        flow.template_matcher.scores.return_value = {
            "dismantle_empty": 0.2,
            "dismantle_ready": 0.0,
        }

        with self.assertRaisesRegex(TimeoutError, "8 one-second click attempts"):
            flow.open_dismantle_panel_with_retries(
                window,
                match("dismantle_open"),
            )

        self.assertEqual(flow.click_template.call_count, 8)
        self.assertEqual(sleep.call_count, 8)


if __name__ == "__main__":
    unittest.main()
