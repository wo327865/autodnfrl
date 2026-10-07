import unittest
from unittest.mock import Mock, patch

from autodnf_py.workflow import AutoDNF


class RewardExplorationTests(unittest.TestCase):
    @patch("autodnf_py.workflow.time.sleep")
    def test_right_edge_waits_one_second_without_exploring_left(self, sleep):
        flow = AutoDNF.__new__(AutoDNF)
        flow.debug = False
        flow.client = Mock()
        window = object()
        flow.client.find_window.return_value = window
        flow.client.world_phase_frame.return_value = object()
        flow.client.phase_camera_motion.return_value = (0.0, 0.0, 0.5)
        flow.detect_reward_pile = Mock(return_value=None)

        self.assertTrue(flow.explore_for_rewards())

        self.assertEqual(
            [call.args for call in flow.client.hold.call_args_list],
            [([124], 0.45), ([124], 0.45)],
        )
        self.assertNotIn(([123], 0.45), [call.args for call in flow.client.hold.call_args_list])
        self.assertEqual(sleep.call_args_list[-1].args, (1.0,))

    def test_collection_returns_to_battle_cycle_after_right_edge(self):
        flow = AutoDNF.__new__(AutoDNF)
        flow.client = Mock()
        flow.client.find_window.return_value = object()
        flow.wait_for_reward_pile = Mock(return_value=None)
        flow.explore_for_rewards = Mock(return_value=True)

        flow.collect_visible_rewards()

        flow.explore_for_rewards.assert_called_once_with()
        flow.client.spiral_drag.assert_not_called()


if __name__ == "__main__":
    unittest.main()
