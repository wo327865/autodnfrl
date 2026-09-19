import unittest
from unittest.mock import Mock, patch

from autodnf_py.macos import TextBox
from autodnf_py.workflow import AutoDNF


class TournamentPopupTests(unittest.TestCase):
    def make_flow(self, execute=True):
        flow = AutoDNF.__new__(AutoDNF)
        flow.client = Mock()
        flow.client.execute = execute
        flow.in_dungeon = False
        return flow

    @patch("autodnf_py.workflow.time.sleep")
    def test_closes_uniquely_identified_tournament_ad(self, sleep):
        flow = self.make_flow()
        window = object()
        boxes = [
            TextBox("2026DNF手游全国格斗大赛秋季赛", 0.35, 0.08, 0.3, 0.04)
        ]

        self.assertTrue(flow.dismiss_known_tournament_popup(window, boxes))
        flow.client.click.assert_called_once_with(
            window,
            (0.93, 0.92),
            "close tournament advertisement",
        )
        sleep.assert_called_once_with(0.8)

    def test_does_not_click_without_gate_or_during_dungeon(self):
        flow = self.make_flow()
        self.assertFalse(
            flow.dismiss_known_tournament_popup(
                object(),
                [TextBox("全国格斗", 0.4, 0.4, 0.1, 0.04)],
            )
        )
        flow.in_dungeon = True
        self.assertFalse(
            flow.dismiss_known_tournament_popup(
                object(),
                [TextBox("全国格斗大赛秋季赛", 0.4, 0.4, 0.2, 0.04)],
            )
        )
        flow.client.click.assert_not_called()

    @patch("autodnf_py.workflow.time.sleep")
    def test_closes_character_activity_ad_at_calibrated_x(self, sleep):
        flow = self.make_flow()
        window = object()
        boxes = [
            TextBox("活动角色福利 01", 0.25, 0.35, 0.2, 0.04),
            TextBox("前往指定活动角色", 0.40, 0.12, 0.2, 0.04),
        ]

        self.assertTrue(flow.dismiss_known_activity_popup(window, boxes))
        flow.client.click.assert_called_once_with(
            window,
            (0.947, 0.907),
            "close activity popup",
        )
        sleep.assert_called_once_with(0.8)

    @patch("autodnf_py.workflow.time.sleep")
    def test_closes_level_boost_ad_at_calibrated_x(self, sleep):
        flow = self.make_flow()
        window = object()
        boxes = [
            TextBox("3分钟直升15万", 0.35, 0.75, 0.3, 0.08),
            TextBox("全能黄金胶囊", 0.65, 0.55, 0.2, 0.04),
            TextBox("本角色已使用", 0.40, 0.30, 0.2, 0.04),
        ]

        self.assertTrue(flow.dismiss_known_level_boost_popup(window, boxes))
        flow.client.click.assert_called_once_with(
            window,
            (0.884, 0.895),
            "close level-boost advertisement",
        )
        sleep.assert_called_once_with(0.8)

    def test_level_boost_ad_requires_two_unique_markers(self):
        flow = self.make_flow()
        boxes = [TextBox("查看更多活动", 0.6, 0.1, 0.2, 0.04)]

        self.assertFalse(flow.dismiss_known_level_boost_popup(object(), boxes))
        flow.client.click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
