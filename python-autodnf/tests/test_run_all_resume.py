import unittest
from unittest.mock import Mock

from autodnf_py.macos import TextBox
from autodnf_py.workflow import AutoDNF


def box(text, x=0.4, y=0.4):
    return TextBox(text, x, y, 0.08, 0.03)


class RunAllResumeTests(unittest.TestCase):
    def test_active_room_and_boss_results(self):
        for boxes in (
            [box("秘境：侵蚀之地", 0.85, 0.92)],
            [box("再次挑战", 0.84, 0.8)],
            [box("领奖结算", 0.84, 0.7)],
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
        ):
            self.assertFalse(AutoDNF.can_resume_dungeon(boxes))

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
