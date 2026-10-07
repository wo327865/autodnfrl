import unittest
from unittest.mock import Mock

from autodnf_py.macos import TextBox
from autodnf_py.workflow import AutoDNF, CharacterBoardRow


def box(text, x=0.4, y=0.4):
    return TextBox(text, x, y, 0.08, 0.03)


class RunAllResumeTests(unittest.TestCase):
    @unittest.mock.patch("autodnf_py.workflow.time.sleep")
    def test_character_row_must_visually_change_before_start_is_allowed(self, sleep):
        flow = AutoDNF.__new__(AutoDNF)
        flow.client = Mock()
        window = object()
        flow.client.find_window.return_value = window
        flow.client.capture_png_bytes.side_effect = [b"unchanged", b"selected"]
        flow.client.region_difference.side_effect = [0.2, 4.0]
        row = CharacterBoardRow(
            top=0.30,
            bottom=0.42,
            click_y=0.64,
            level=80,
            fatigue=100,
            online=False,
        )

        selected, result_window = flow.select_character_board_row(
            window,
            row,
            b"before",
        )

        self.assertTrue(selected)
        self.assertIs(result_window, window)
        self.assertEqual(flow.client.click.call_count, 2)
        self.assertEqual(
            [call.args[2] for call in flow.client.click.call_args_list],
            ["select available character (1/8)", "select available character (2/8)"],
        )

    def test_generic_ui_transition_retries_eight_times_at_one_second(self):
        flow = AutoDNF.__new__(AutoDNF)
        flow.client = Mock()
        window = object()
        source_boxes = [box("委托")]
        flow.wait_for = Mock(return_value=(window, source_boxes))
        flow.wait_for_any = Mock(side_effect=TimeoutError("no transition"))
        flow.dismiss_known_blocking_popup = Mock(return_value=False)
        flow.client.find_window.return_value = window
        flow.client.ocr.return_value = source_boxes

        with self.assertRaisesRegex(TimeoutError, "委托 did not reach"):
            flow.click_then_wait(
                "委托",
                "main screen",
                ["委托"],
                {"commission board": ["深渊：时空秘境"]},
            )

        self.assertEqual(flow.client.click.call_count, 8)
        self.assertEqual(
            [call.kwargs["timeout"] for call in flow.wait_for_any.call_args_list],
            [1.0] * 8,
        )

    def test_character_selection_button_retries_eight_times(self):
        flow = AutoDNF.__new__(AutoDNF)
        flow.client = Mock()
        window = object()
        flow.wait_for_maintenance_town = Mock(return_value=(window, []))
        flow.wait_for_character_selection_board = Mock(
            side_effect=TimeoutError("selection board not ready")
        )

        self.assertFalse(flow.switch_to_available_character())
        self.assertEqual(flow.client.click.call_count, 8)
        self.assertEqual(
            [call.args[2] for call in flow.client.click.call_args_list],
            [f"character selection ({attempt}/8)" for attempt in range(1, 9)],
        )

    def test_active_room_and_boss_results(self):
        for boxes in (
            [box("秘境：侵蚀之地", 0.85, 0.92)],
            [box("再次挑战", 0.84, 0.8)],
            [box("领奖结算", 0.84, 0.7)],
            [box("100/100", 0.08, 0.70), box("80/100", 0.08, 0.58)],
        ):
            self.assertTrue(AutoDNF.can_resume_dungeon(boxes))

    def test_non_dungeon_screens(self):
        for boxes in (
            [],
            [box("委托"), box("选角")],
            [box("入场"), box("100/100")] * 3,
            [box("挑战进度"), box("100/100")],
            [box("秘境：侵蚀之地", 0.2, 0.4)],
            [box("再次挑战", 0.2, 0.4)],
            [box("100/100", 0.08, 0.70)],
        ):
            self.assertFalse(AutoDNF.can_resume_dungeon(boxes))

    def test_startup_probe_survives_one_unreadable_frame(self):
        flow = AutoDNF.__new__(AutoDNF)
        flow.client = Mock()
        flow.client.ocr.side_effect = [
            [],
            [box("0/100", 0.08, 0.70), box("50/100", 0.08, 0.58)],
        ]

        with unittest.mock.patch("autodnf_py.workflow.time.sleep"):
            self.assertTrue(flow.starts_in_active_dungeon())
        self.assertEqual(flow.client.ocr.call_count, 2)

    def test_resume_before_town_rotation_without_entering_again(self):
        flow = AutoDNF.__new__(AutoDNF)
        flow.client = Mock()
        flow.client.ocr.return_value = [box("秘境：侵蚀之地", 0.85, 0.92)]
        events = []
        flow.run_battle = Mock(side_effect=lambda **kw: events.append("battle"))
        flow.wait_for_town_fatigue = Mock(
            side_effect=lambda: events.append("town") or 0
        )
        flow.switch_to_available_character = Mock(return_value=False)
        flow.run_all_characters()
        self.assertEqual(events, ["battle", "town"])
        flow.run_battle.assert_called_once_with(start_by_entering=False)

    def test_town_start_does_not_resume_battle(self):
        flow = AutoDNF.__new__(AutoDNF)
        flow.client = Mock()
        flow.client.ocr.return_value = [box("委托"), box("选角")]
        flow.run_battle = Mock()
        flow.wait_for_town_fatigue = Mock(return_value=0)
        flow.switch_to_available_character = Mock(return_value=False)
        flow.run_all_characters()
        flow.run_battle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
