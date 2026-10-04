"""Permanent offline regressions. Native input and global hooks are mocked."""
import copy
import queue
import threading
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from selected_level_retry.app import HotkeyBridge, run_app
from selected_level_retry.config import load_feature_config
from selected_level_retry.controller import RetryStats
from selected_level_retry.ui import SelectedLevelRetryUI
from tower_bot.input_controller import DryRunBackend, InputController, PyAutoGuiBackend
from tower_bot.models import ActionType, BotError, BotState, Point, Rect, WindowInfo
from tower_bot.state_machine import StateMachine


class InputSafetyTests(unittest.TestCase):
    def setUp(self):
        self.cfg, _ = load_feature_config()
        self.enabled = True
        self.window = WindowInfo(hwnd=123, title='test', rect=Rect(0, 0, 550, 1020))
        self.locator = Mock()
        self.locator.current_rect.return_value = self.window.rect
        self.locator.is_foreground.return_value = True
        self.locator.focus.return_value = True
        self.backend = DryRunBackend()
        self.input = InputController(self.cfg, self.locator, backend=self.backend,
                                     enabled_check=lambda: self.enabled)

    def click(self):
        return self.input.click_reference(self.window, Point(100, 100),
                 action=ActionType.CLICK_START, reason='regression', allow_jitter=False)

    def test_normal_click_uses_expected_coordinates(self):
        self.click()
        self.assertEqual(self.backend.clicks, [(100, 100)])

    def test_pause_during_focus_sends_no_click(self):
        def focus(_):
            self.enabled = False
            return True
        self.locator.focus.side_effect = focus
        with self.assertRaises(BotError):
            self.click()
        self.assertEqual(self.backend.clicks, [])

    def test_geometry_changed_during_focus_sends_no_click(self):
        self.locator.current_rect.side_effect = [self.window.rect, Rect(10, 0, 560, 1020)]
        with self.assertRaises(BotError):
            self.click()
        self.assertEqual(self.backend.clicks, [])

    def test_stale_window_from_capture_sends_no_click(self):
        self.locator.current_rect.return_value = Rect(10, 0, 560, 1020)
        with self.assertRaises(BotError):
            self.click()

    def test_focus_lost_before_dispatch_sends_no_click(self):
        self.locator.is_foreground.side_effect = [True, False]
        with self.assertRaises(BotError):
            self.click()
        self.assertEqual(self.backend.clicks, [])

    def test_expired_frame_sends_no_click(self):
        with patch('tower_bot.input_controller.time.monotonic', side_effect=[10, 10, 12]):
            with self.assertRaises(BotError):
                self.click()
        self.assertEqual(self.backend.clicks, [])

    def test_paused_mouse_return_is_blocked(self):
        self.enabled = False
        with self.assertRaises(BotError):
            self.input.move_reference(self.window, Point(100, 100), reason='test')
        self.assertEqual(self.backend.moves, [])

    def test_pause_during_focus_blocks_swipe_and_scroll(self):
        def focus(_):
            self.enabled = False
            return True
        for action in ('swipe', 'scroll'):
            self.enabled = True
            self.locator.focus.side_effect = focus
            with self.assertRaises(BotError):
                if action == 'swipe':
                    self.input.swipe_reference(self.window, Point(100, 200), Point(100, 100),
                        action=ActionType.CLICK_START, reason='test', duration=0.2)
                else:
                    self.input.scroll_reference(self.window, Point(100, 100), clicks=-1,
                        action=ActionType.CLICK_START, reason='test')
        self.assertEqual(self.backend.drags, [])
        self.assertEqual(self.backend.scrolls, [])

    def test_native_scroll_checks_again_after_moving(self):
        backend = PyAutoGuiBackend(lambda: self.enabled)
        def move(*args, **kwargs):
            self.enabled = False
        with patch('tower_bot.input_controller.pyautogui.moveTo', side_effect=move), \
             patch('tower_bot.input_controller.pyautogui.scroll') as scroll:
            with self.assertRaises(BotError):
                backend.scroll(100, 100, -1)
            scroll.assert_not_called()

    def test_cancel_drag_releases_owned_button(self):
        backend = PyAutoGuiBackend(lambda: self.enabled)
        def pause(*_):
            self.enabled = False
        with patch('tower_bot.input_controller.pyautogui.moveTo'), \
             patch('tower_bot.input_controller.pyautogui.mouseDown'), \
             patch('tower_bot.input_controller.pyautogui.mouseUp') as release, \
             patch('tower_bot.input_controller.time.sleep', side_effect=pause):
            with self.assertRaises(BotError):
                backend.drag(100, 200, 100, 100, 0.2)
            release.assert_called_once()


class RecognitionTests(unittest.TestCase):
    def test_blank_frames_never_become_confirmation(self):
        cfg, _ = load_feature_config()
        for shade in (0, 180, 255):
            frame = np.full((1020, 550, 3), shade, np.uint8)
            self.assertFalse(StateMachine._looks_like_confirm_dialog(frame))
            analysis = StateMachine(cfg).analyze_frame(frame, debounce=False)
            self.assertEqual(analysis.state, BotState.UNKNOWN)
            self.assertEqual(analysis.next_action, ActionType.NONE)

    def test_debounce_requires_consecutive_current_evidence(self):
        machine = object.__new__(StateMachine)
        machine.cfg = {'actions': {'debounce_required': 2}}
        machine._stable_state = BotState.CONFIRM_CHALLENGE
        machine._history = deque([BotState.CONFIRM_CHALLENGE, BotState.UNKNOWN])
        self.assertEqual(machine._debounced_state(BotState.UNKNOWN), BotState.UNKNOWN)
        machine._history = deque([BotState.CONFIRM_CHALLENGE] * 2)
        self.assertEqual(machine._debounced_state(BotState.CONFIRM_CHALLENGE), BotState.CONFIRM_CHALLENGE)


class LifecycleTests(unittest.TestCase):
    def test_escape_is_registered(self):
        controller = Mock(cfg={'hotkeys': {'toggle': '<f8>', 'stop': '<f9>', 'emergency_pause': '<esc>'}})
        with patch('pynput.keyboard.GlobalHotKeys') as listener:
            bridge = HotkeyBridge(controller)
            self.assertTrue(bridge.start())
            mapping = listener.call_args.args[0]
            self.assertEqual(set(mapping), {'<f8>', '<f9>', '<esc>'})
            mapping['<esc>']()
            controller.emergency_pause.assert_called_once()
            bridge.stop()

    def test_headless_start_failure_returns_instead_of_spinning(self):
        with patch('selected_level_retry.app.setup_logging'), \
             patch('selected_level_retry.app.SelectedLevelRetryController') as controller:
            controller.return_value.enabled.is_set.return_value = False
            self.assertEqual(run_app(['--no-gui', '--no-hotkeys']), 1)
            controller.return_value.join.assert_not_called()
            controller.return_value.shutdown.assert_called_once()

    def test_worker_stats_use_bounded_immutable_queue_without_tk_calls(self):
        ui = object.__new__(SelectedLevelRetryUI)
        ui._updates = queue.Queue(maxsize=1)
        ui._closing = False
        ui.root = Mock()
        stats = RetryStats(attempts=1)
        ui._on_stats_threadsafe(stats)
        stats.attempts = 2
        self.assertEqual(ui._updates.get_nowait().attempts, 1)
        for value in range(100):
            ui._on_stats_threadsafe(RetryStats(attempts=value))
        self.assertEqual(ui._updates.qsize(), 1)
        self.assertEqual(ui._updates.get_nowait().attempts, 99)
        ui.root.after.assert_not_called()


if __name__ == '__main__':
    unittest.main()
