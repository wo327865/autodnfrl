import unittest
from unittest.mock import Mock, call

from autodnf_py.workflow import AutoDNF


class CommissionNavigationTests(unittest.TestCase):
    def test_opens_relocated_daily_dungeon_before_rift(self):
        flow = AutoDNF.__new__(AutoDNF)
        flow.click_then_wait = Mock(
            side_effect=[
                ("commission board", object(), []),
                ("realm selection (normal)", object(), []),
            ]
        )

        self.assertEqual(
            flow.open_rift_commission("commission categories"),
            "realm selection",
        )
        self.assertEqual(
            flow.click_then_wait.call_args_list[0],
            call(
                "日常地下城",
                "commission categories",
                ["委托布告栏", "日常地下城"],
                {"commission board": ["深渊：时空秘境"]},
            ),
        )
        self.assertEqual(
            flow.click_then_wait.call_args_list[1].args[0],
            "深渊：时空秘境",
        )

    def test_existing_rift_board_does_not_click_category(self):
        flow = AutoDNF.__new__(AutoDNF)
        flow.click_then_wait = Mock(
            return_value=("travel confirmation", object(), [])
        )

        self.assertEqual(
            flow.open_rift_commission("commission board"),
            "travel confirmation",
        )
        self.assertEqual(flow.click_then_wait.call_count, 1)
        self.assertEqual(
            flow.click_then_wait.call_args.args[0],
            "深渊：时空秘境",
        )


if __name__ == "__main__":
    unittest.main()
