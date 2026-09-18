import unittest
from unittest.mock import Mock

from autodnf_py.macos import TextBox
from autodnf_py.workflow import AutoDNF, CurrentCharacterFatigueExhausted


def box(text, x):
    return TextBox(text, x, 0.25, 0.06, 0.04)


class MainCharacterFatigueTests(unittest.TestCase):
    def test_reads_crowned_middle_card_instead_of_companions(self):
        boxes = [box("100/100", 0.45), box("0/100", 0.65), box("100/100", 0.84)]
        self.assertEqual(AutoDNF.current_formation_fatigue(boxes), 0)

    def test_existing_formation_is_rejected_before_entry(self):
        flow = AutoDNF.__new__(AutoDNF)
        window = object()
        boxes = [
            box("普通秘境", 0.1),
            box("入场", 0.85),
            box("100/100", 0.45),
            box("0/100", 0.65),
            box("100/100", 0.84),
        ]
        flow.wait_for = Mock(return_value=(window, boxes))

        with self.assertRaises(CurrentCharacterFatigueExhausted):
            flow.configure_party()

    def test_entry_guard_does_not_click_with_exhausted_main(self):
        flow = AutoDNF.__new__(AutoDNF)
        flow.wait_for = Mock(
            return_value=(object(), [box("入场", 0.85), box("0/100", 0.65)])
        )
        flow.click_fresh_entry_button = Mock()

        with self.assertRaises(CurrentCharacterFatigueExhausted):
            flow.enter_dungeon()
        flow.click_fresh_entry_button.assert_not_called()

    def test_run_all_returns_to_town_then_rotates(self):
        flow = AutoDNF.__new__(AutoDNF)
        flow.client = Mock()
        flow.client.ocr.return_value = [box("委托", 0.1), box("选角", 0.1)]
        flow.wait_for_town_fatigue = Mock(side_effect=[100, 0])
        flow.run_to_party = Mock(side_effect=CurrentCharacterFatigueExhausted("0/100"))
        flow.return_from_abyss_pages_for_maintenance = Mock()
        flow.switch_to_available_character = Mock(return_value=False)

        flow.run_all_characters()

        flow.return_from_abyss_pages_for_maintenance.assert_called_once_with()
        flow.switch_to_available_character.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
