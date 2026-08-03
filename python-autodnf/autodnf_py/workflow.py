from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .detector import LootPileDetector
from .macos import MacClient, TextBox, Window
from .template_matcher import FixedTemplateMatcher, TemplateMatch
from .vision_fallback import (
    SAFE_CLICK_TEXTS,
    GeminiVisionFallback,
    VisionAdvisor,
    VisionFallbackError,
)


def find(text: str, boxes: list[TextBox]) -> list[TextBox]:
    target = TextBox(text, 0, 0, 0, 0).normalized
    return [box for box in boxes if target in box.normalized]


def exact(text: str, boxes: list[TextBox]) -> list[TextBox]:
    target = TextBox(text, 0, 0, 0, 0).normalized
    return [box for box in boxes if box.normalized == target]


@dataclass(frozen=True)
class CharacterBoardRow:
    """One fully visible, geometry-calibrated character-board row."""

    top: float
    bottom: float
    click_y: float
    level: int | None
    fatigue: int | None
    online: bool


class AutoDNF:
    # This full-screen event promotion blocks the town UI but has a stable,
    # harmless close X. Its text is used as the required local gate; the click
    # never happens for an arbitrary unrecognised overlay.
    ACTIVITY_POPUP_TEXTS = ("活动角色福利", "前往指定活动角色")
    ACTIVITY_POPUP_CLOSE = (0.94, 0.85)
    # This harmless informational dialog can appear shortly after switching
    # characters. Gate the generic 确认 button behind unique guild-sign-in text
    # so an unrelated confirmation can never be accepted automatically.
    GUILD_SIGNIN_POPUP_TEXTS = ("每天最多", "公会签到")
    # This post-login summary is also informational: Normal Realm experience
    # has already been granted and its items were sent to mail. The generic
    # 确认 button is safe only when these distinctive message fragments coexist.
    RIFT_REWARD_MAIL_POPUP_TEXTS = ("普通秘境", "经验值", "邮件发放")
    # Post-login 星源石 acquisition promotion. Its artwork varies by the
    # awarded stone, but the congratulation/button vocabulary and close-X
    # position are stable.
    EPIC_STONE_POPUP_TEXTS = (
        "星源石",
        "恭喜冒险家",
        "冻结心",
        "技能攻击力",
        "祝贺老板",
    )
    EPIC_STONE_POPUP_CLOSE = (0.835, 0.808)
    # Full-screen 8-day special sign-in artwork has no visible close control.
    # It is identified by both its headline and repeated day-card labels.
    SPECIAL_SIGNIN_POPUP_HEADLINES = ("8日特别签到", "新深渊")
    SPECIAL_SIGNIN_DISMISS_POINT = (0.50, 0.55)
    # The town's 选角 text is small and Vision can return a box shifted onto
    # the button's decorative arrow. This is the stable centre of the actual
    # top-left button, expressed in window-normalized Vision coordinates.
    CHARACTER_SELECT_POINT = (0.04, 0.82)
    # The 挑战进度 board's X is lower than a standard dialog title-bar X.
    CHARACTER_BOARD_CLOSE_POINT = (0.94, 0.87)
    # Calibrated from 2956x1718 window-only captures. Coordinates below use a
    # top-left origin; the runtime scales them to the current window size.
    CHARACTER_BOARD_PANEL = (0.078, 0.253, 0.922, 0.827)
    CHARACTER_BOARD_ROW_PITCH = 214 / 1718
    CHARACTER_BOARD_LEVEL_STRIP = (0.078, 0.145)
    CHARACTER_BOARD_FATIGUE_STRIP = (0.285, 0.385)
    CHARACTER_BOARD_ONLINE_STRIP = (0.078, 0.175)
    # The battle companion picker is a fixed three-column rolling grid.
    # Vision coordinates use a bottom-left origin, while the panel rectangle
    # used for image comparison uses a top-left origin.
    PARTY_PICKER_PANEL = (0.14, 0.35, 0.88, 0.76)
    PARTY_PICKER_ROW_PITCH = 0.16
    PARTY_PICKER_ROW_CENTERS = (0.57, 0.41, 0.25)
    PARTY_PICKER_COLUMN_CENTERS = (0.267, 0.511, 0.755)
    # Top-left-origin regions containing the two companion portraits above
    # the picker grid. They provide a visual fallback when OCR misses the
    # 选择完成 marker after a successful card click.
    PARTY_PICKER_COMPANION_SLOTS = (
        (0.39, 0.22, 0.47, 0.35),
        (0.55, 0.22, 0.63, 0.35),
    )
    # Page-level back arrows (邮箱 / 背包) sit below and right of the app
    # window's outer top-left edge.
    PAGE_BACK_POINT = (0.04, 0.94)
    STORY_SKIP_FALLBACK_POINT = (0.94, 0.93)
    DISMANTLE_TEMPLATE_NAMES = (
        "dismantle_open",
        "dismantle_ready",
        "dismantle_empty",
        "dismantle_confirm",
        "dismantle_close",
        "inventory_ready",
    )
    DISMANTLE_CLICKABLE_TEMPLATES = (
        "dismantle_open",
        "dismantle_ready",
        "dismantle_confirm",
        "dismantle_close",
    )
    def __init__(
        self,
        client: MacClient,
        debug: bool = False,
        vision_fallback: VisionAdvisor | None = None,
        vision_auto_act: bool = False,
        template_matcher: FixedTemplateMatcher | None = None,
    ) -> None:
        self.client = client
        self.debug = debug
        self.loot_detector = LootPileDetector()
        self.vision_fallback = vision_fallback
        self.vision_auto_act = vision_auto_act
        self.template_matcher = template_matcher
        self.in_dungeon = False
        self.special_signin_dismiss_attempts = 0
        self.character_traversal_started = False

    def wait_for(self, texts: list[str], state: str, timeout: float = 18) -> tuple[Window, list[TextBox]]:
        _, window, boxes = self.wait_for_any({state: texts}, timeout)
        return window, boxes

    def wait_for_character_selection_board(
        self,
        timeout: float = 18,
    ) -> tuple[Window, list[TextBox]]:
        """Recognise the character board even when its title is obscured.

        Login announcements can temporarily cover 挑战进度 while leaving the
        table columns, repeated row fields, and bottom controls visible. Each
        alternative below requires multiple board-specific anchors so a town
        screen cannot satisfy the check through one incidental OCR match.
        """
        _, window, boxes = self.wait_for_any(
            {
                "character selection board header": ["挑战进度"],
                "character selection board columns": ["疲劳值", "神秘商店"],
                "character selection board rows": ["可刷新", "每日1/1"],
                "character selection board controls": [
                    "玩法设置",
                    "选择角色",
                    "开始游戏",
                ],
            },
            timeout,
        )
        return window, boxes

    def wait_for_any(
        self,
        states: dict[str, list[str]],
        timeout: float = 18,
    ) -> tuple[str, Window, list[TextBox]]:
        """Poll until any valid screen state is visible.

        The delay increases gently from 0.4 to 1.2 seconds. This avoids busy
        polling during a loading transition while still responding promptly to
        a fast dialog. Callers model every legitimate next screen instead of
        assuming that one particular popup must appear.
        """
        deadline = time.monotonic() + timeout
        delay = 0.4
        vision_attempted = False
        last_window: Window | None = None
        last_boxes: list[TextBox] = []
        while True:
            if time.monotonic() >= deadline:
                if (
                    not vision_attempted
                    and self.vision_fallback is not None
                    and not self.in_dungeon
                    and last_window is not None
                ):
                    vision_attempted = True
                    if self.try_vision_recovery(states, last_window, last_boxes):
                        deadline = time.monotonic() + min(timeout, 12)
                        delay = 0.4
                        continue
                break
            window = self.client.find_window()
            boxes = self.client.ocr(window)
            last_window, last_boxes = window, boxes
            if self.dismiss_known_special_signin_popup(window, boxes):
                delay = 0.4
                continue
            if self.dismiss_known_rift_reward_mail_popup(window, boxes):
                delay = 0.4
                continue
            if self.dismiss_known_guild_signin_popup(window, boxes):
                delay = 0.4
                continue
            if self.dismiss_known_epic_stone_popup(window, boxes):
                delay = 0.4
                continue
            # A known town activity promotion can appear before any workflow
            # action. Dismiss it immediately rather than waiting for the
            # current state timeout and sending an unnecessary cloud request.
            if self.dismiss_known_activity_popup(window, boxes):
                delay = 0.4
                continue
            for state, texts in states.items():
                if all(find(text, boxes) for text in texts):
                    print(f"Detected {state}")
                    return state, window, boxes
            time.sleep(delay)
            delay = min(1.2, delay * 1.25)
        names = ", ".join(states)
        raise TimeoutError(f"Timed out waiting for one of: {names}")

    def try_vision_recovery(
        self,
        expected_states: dict[str, list[str]],
        window: Window,
        boxes: list[TextBox],
    ) -> bool:
        """Ask the model once, then locally gate any suggested action."""
        assert self.vision_fallback is not None
        try:
            screenshot = self.client.capture_png_bytes(window)
            advice = self.vision_fallback.analyze(
                screenshot,
                expected_states,
                [box.text for box in boxes],
            )
        except (VisionFallbackError, OSError, ValueError) as error:
            print(f"AI vision fallback unavailable: {error}")
            return False

        print(
            f"AI vision fallback: {advice.obstruction}; "
            f"suggested {advice.action} (confidence {advice.confidence:.2f})"
        )
        print(f"AI reason: {advice.reason}")

        if advice.action == "wait":
            print("AI fallback is waiting 3 seconds before rechecking known states")
            time.sleep(3)
            return True
        if advice.action == "ask_human":
            print("AI fallback requested human help; no action was taken")
            return False
        if not self.vision_auto_act:
            print(
                "AI fallback advice only; rerun with --vision-auto-act to allow "
                "locally validated safe clicks"
            )
            return False
        if not self.client.execute:
            print("AI fallback did not click because --execute is not enabled")
            return False

        # The API response can take seconds. Resolve all actions against a
        # fresh frame so stale OCR boxes or coordinates cannot be clicked.
        window = self.client.find_window()
        boxes = self.client.ocr(window)
        if advice.action == "click_text":
            if advice.target_text not in SAFE_CLICK_TEXTS:
                print(f"Rejected unsafe AI button text {advice.target_text!r}")
                return False
            choices = exact(advice.target_text, boxes)
            if len(choices) != 1:
                if self.try_login_story_skip(advice, expected_states, screenshot, window):
                    return True
                print(
                    f"Rejected AI click: expected one exact {advice.target_text!r}, "
                    f"found {len(choices)}"
                )
                return False
            self.client.click(
                window,
                choices[0].center,
                f"AI-safe {advice.target_text}",
            )
            time.sleep(1)
            return True

        # A visual X often has no OCR label. Require high confidence and a
        # top-right popup position; this rejects arbitrary model coordinates.
        if (
            advice.action == "close_popup"
            and advice.confidence >= 0.90
            and 0.55 <= advice.point_x <= 0.96
            and 0.04 <= advice.point_y <= 0.42
        ):
            current_screenshot = self.client.capture_png_bytes(window)
            if not GeminiVisionFallback.close_region_is_stable(
                screenshot,
                current_screenshot,
                advice.point_x,
                advice.point_y,
            ):
                print("Rejected AI popup close because the visual target changed")
                return False
            self.client.click(
                window,
                (advice.point_x, 1.0 - advice.point_y),
                "AI-safe popup close",
            )
            time.sleep(1)
            return True
        print("Rejected AI popup-close point because it failed local safety checks")
        return False

    def try_login_story_skip(
        self,
        advice,
        expected_states: dict[str, list[str]],
        before_png: bytes,
        window: Window,
    ) -> bool:
        """Allow only an explicit high-confidence story Skip during character login.

        Story scenes often render their small 跳过 label too stylised for local
        OCR. Gemini must name the exact harmless button and identify a story
        obstruction; its point must be in the conventional top-right Skip
        region. This is intentionally unavailable to every other action.
        """
        text = f"{advice.obstruction} {advice.reason}".lower()
        is_story = any(marker in text for marker in ("story", "cutscene", "剧情", "对话"))
        is_login_wait = "new character logged into town" in expected_states
        if not (
            advice.target_text == "跳过"
            and advice.confidence >= 0.95
            and is_story
            and is_login_wait
        ):
            return False
        if 0.65 <= advice.point_x <= 0.99 and 0.02 <= advice.point_y <= 0.30:
            point = (advice.point_x, 1.0 - advice.point_y)
        else:
            # Older model prompts returned -1 for click_text coordinates. The
            # fixed point is only used under every gate above.
            point = self.STORY_SKIP_FALLBACK_POINT
        current_png = self.client.capture_png_bytes(window)
        # The visual target must still be present at the suggested point after
        # the model request; this rejects a stale loading/cutscene frame.
        if not GeminiVisionFallback.close_region_is_stable(
            before_png,
            current_png,
            point[0],
            1.0 - point[1],
        ):
            print("Rejected story Skip because its top-right target changed")
            return False
        self.client.click(window, point, "AI-safe story Skip")
        time.sleep(1)
        return True

    def click_text(self, text: str, state: str, require: list[str] | None = None) -> None:
        window, boxes = self.wait_for([text, *(require or [])], state)
        self.click_from_boxes(text, window, boxes, text)

    def click_from_boxes(self, text: str, window: Window, boxes: list[TextBox], label: str) -> None:
        choices = exact(text, boxes) or find(text, boxes)
        if len(choices) != 1:
            raise RuntimeError(f"Expected one {text!r}, found {len(choices)}")
        self.client.click(window, choices[0].center, label)

    def click_then_wait(
        self,
        text: str,
        source_state: str,
        source_texts: list[str],
        next_states: dict[str, list[str]],
        retries: int = 3,
    ) -> tuple[str, Window, list[TextBox]]:
        """Click an action and retry only if its source screen remains.

        A failed click due to a transient frame/network delay leaves the source
        state on screen. A slow successful transition instead gets extra time
        to render before any retry is attempted.
        """
        for attempt in range(1, retries + 1):
            window, boxes = self.wait_for(source_texts, source_state)
            if self.dismiss_known_activity_popup(window, boxes):
                print("Dismissed activity popup; retrying the original action")
                continue
            self.click_from_boxes(text, window, boxes, f"{text} ({attempt}/{retries})")
            time.sleep(0.7)
            try:
                return self.wait_for_any(next_states, timeout=9)
            except TimeoutError:
                # Retry only after positively seeing the original screen again.
                try:
                    self.wait_for(source_texts, source_state, timeout=3)
                except TimeoutError:
                    # The old screen is gone: continue waiting rather than
                    # issuing a duplicate click into an unknown loading state.
                    return self.wait_for_any(next_states, timeout=12)
                print(f"{text} did not transition; retrying ({attempt}/{retries})")
        names = ", ".join(next_states)
        raise TimeoutError(f"{text} did not reach: {names}")

    def dismiss_known_activity_popup(self, window: Window, boxes: list[TextBox]) -> bool:
        """Close the known activity promotion only when its local OCR gate is present."""
        if self.in_dungeon or not self.client.execute:
            return False
        if not all(find(text, boxes) for text in self.ACTIVITY_POPUP_TEXTS):
            return False
        print("Detected known activity popup; clicking its close X")
        self.client.click(window, self.ACTIVITY_POPUP_CLOSE, "close activity popup")
        time.sleep(0.8)
        return True

    def dismiss_known_guild_signin_popup(
        self,
        window: Window,
        boxes: list[TextBox],
    ) -> bool:
        """Confirm only the uniquely identified daily guild-sign-in notice."""
        if self.in_dungeon or not self.client.execute:
            return False
        if not all(find(text, boxes) for text in self.GUILD_SIGNIN_POPUP_TEXTS):
            return False
        choices = [
            box
            for box in exact("确认", boxes)
            if 0.35 <= box.center[0] <= 0.65 and 0.20 <= box.center[1] <= 0.55
        ]
        if len(choices) != 1:
            if self.debug:
                print(
                    "Detected guild-sign-in notice text but could not isolate "
                    f"one safe 确认 button (found {len(choices)})"
                )
            return False
        print("Detected daily guild-sign-in notice; clicking 确认")
        self.client.click(window, choices[0].center, "confirm guild-sign-in notice")
        time.sleep(0.8)
        return True

    def dismiss_known_rift_reward_mail_popup(
        self,
        window: Window,
        boxes: list[TextBox],
    ) -> bool:
        """Confirm only the uniquely identified post-login rift reward notice."""
        if self.in_dungeon or not self.client.execute:
            return False
        if not all(find(text, boxes) for text in self.RIFT_REWARD_MAIL_POPUP_TEXTS):
            return False
        choices = [
            box
            for box in exact("确认", boxes)
            if 0.35 <= box.center[0] <= 0.65 and 0.20 <= box.center[1] <= 0.55
        ]
        if len(choices) != 1:
            if self.debug:
                print(
                    "Detected Normal Realm reward-mail notice but could not "
                    f"isolate one safe 确认 button (found {len(choices)})"
                )
            return False
        print("Detected Normal Realm reward-mail notice; clicking 确认")
        self.client.click(window, choices[0].center, "confirm rift reward-mail notice")
        time.sleep(0.8)
        return True

    def dismiss_known_epic_stone_popup(
        self,
        window: Window,
        boxes: list[TextBox],
    ) -> bool:
        """Close the post-login 星源石 promotion at its stable upper-right X."""
        # Unlike generic popup recovery, this uniquely identified promotion is
        # safe to close even while the battle loop's in_dungeon flag remains
        # true during the transition back to town.
        if not self.client.execute:
            return False
        visible = sum(
            bool(find(text, boxes))
            for text in self.EPIC_STONE_POPUP_TEXTS
        )
        # Require two independent artwork labels so ordinary town text can
        # never trigger the fixed close-point click.
        if visible < 2:
            return False
        print("Detected post-login 星源石 promotion; clicking its close X")
        self.client.click(
            window,
            self.EPIC_STONE_POPUP_CLOSE,
            "close 星源石 promotion",
        )
        time.sleep(0.8)
        return True

    def dismiss_known_special_signin_popup(
        self,
        window: Window,
        boxes: list[TextBox],
    ) -> bool:
        """Dismiss the uniquely identified 8-day sign-in artwork."""
        if self.in_dungeon or not self.client.execute:
            return False
        headline_visible = any(
            find(text, boxes) for text in self.SPECIAL_SIGNIN_POPUP_HEADLINES
        )
        visible_days = sum(
            bool(find(f"第{day}天", boxes))
            for day in range(1, 9)
        )
        # Four separate day cards uniquely identify this overlay even when
        # Vision cannot read its glowing headline. If the headline is clear,
        # two day cards are sufficient for the same reason.
        if visible_days < 4 and not (headline_visible and visible_days >= 2):
            return False
        self.special_signin_dismiss_attempts += 1
        if self.special_signin_dismiss_attempts == 1:
            print("Detected 8-day special sign-in overlay; pressing Back")
            # macOS virtual keycode 53 is Escape, which PlayCover delivers as
            # Android Back. This closes a modal without touching town controls.
            self.client.press(53)
        else:
            print("Special sign-in overlay remained; clicking its center artwork")
            self.client.click(
                window,
                self.SPECIAL_SIGNIN_DISMISS_POINT,
                "dismiss special sign-in artwork",
            )
        time.sleep(0.8)
        return True

    def run_to_party(self, battle: bool = False) -> bool:
        self.click_then_wait(
            "委托",
            "main screen",
            ["委托"],
            {"commission board": ["深渊：时空秘境"]},
        )
        state = self.open_rift_commission()
        if state == "travel confirmation":
            state = self.confirm_travel()
        if state == "时空秘境 town":
            # A brief destination-town state can appear while the character is
            # still walking, immediately before the realm selector opens.
            try:
                state = self.wait_for_realm_selection(timeout=10)
            except TimeoutError:
                state = "时空秘境 town"
        if state == "时空秘境 town":
            # The game sometimes teleports first, then requires the same
            # commission to be opened again from the destination town.
            self.click_then_wait(
                "委托",
                "时空秘境 town",
                ["委托"],
                {"commission board": ["深渊：时空秘境"]},
            )
            state = self.open_rift_commission()
            if state == "travel confirmation":
                state = self.confirm_travel()
        if state != "realm selection":
            raise RuntimeError(f"Unexpected travel result: {state}")
        return self.continue_from_realm_selection(battle)

    def run_all_characters(self) -> None:
        """Run the dungeon, then rotate through every character with fatigue."""
        round_number = 1
        while True:
            current_fatigue = self.wait_for_town_fatigue()
            if current_fatigue is None:
                # The compact HUD occasionally disappears behind a transition
                # or is read incorrectly by OCR. The character board uses
                # large fatigue labels, so it is the safer fallback than
                # aborting the entire daily run.
                print("Town fatigue HUD was unreadable; checking the character board instead")
                if not self.switch_to_available_character():
                    print("No character with at least 10 fatigue was found. Automation complete.")
                    return
                continue
            if current_fatigue < 10:
                print(
                    f"Current character has only {current_fatigue}/100 fatigue; "
                    "switching characters"
                )
                if not self.switch_to_available_character():
                    print("No character with at least 10 fatigue was found. Automation complete.")
                    return
                # Confirm the newly logged-in character independently before
                # entering the commission workflow.
                continue
            print(f"Current character has {current_fatigue}/100 fatigue; continuing to 委托")
            print(f"Starting character round {round_number}")
            if not self.run_to_party(battle=True):
                print("No eligible party companion remained. Automation complete.")
                return
            round_number += 1

    def run_mail_maintenance_all(self) -> None:
        """Claim character mail and dismantle equipment for every eligible role once."""
        processed: set[str] = set()
        completed = 0
        while True:
            # Start once at the top, then traverse only below the calibrated
            # green 在线 row. No character name or OCR fingerprint is used.
            if not self.switch_to_available_character(
                processed=processed,
                reset_to_top=(completed == 0),
                minimum_fatigue=0,
                reuse_current_if_first=(completed == 0),
            ):
                print(f"Mail and dismantle workflow complete for {completed} eligible character(s).")
                return
            completed += 1
            print(f"Starting mail and dismantle workflow for character {completed}")
            if self.receive_all_character_mail():
                self.dismantle_all_available_equipment()
            else:
                print(
                    "Backpack is full; dismantling first to create room, "
                    "then retrying mail once"
                )
                self.dismantle_all_available_equipment()
                if self.receive_all_character_mail():
                    # Newly claimed equipment may itself be dismantleable.
                    self.dismantle_all_available_equipment()
                else:
                    print(
                        "Backpack is still full after dismantling; moving to "
                        "the next character"
                    )

    def receive_all_character_mail(self) -> bool:
        """Open character mail, claim all mail when present, then return to town."""
        self.click_then_wait(
            "邮箱",
            "town before mailbox",
            ["委托", "邮箱"],
            {"mailbox": ["角色邮件"]},
        )
        window, boxes = self.wait_for(["角色邮件"], "mailbox", timeout=15)
        skip_character = bool(find("背包已满", boxes))
        if skip_character:
            print("Detected 背包已满 in mailbox; leaving this character untouched")
        elif find("未收到邮件", boxes):
            print("No character mail to claim")
        else:
            claim = exact("领取全部物品", boxes)
            if len(claim) == 1:
                claim_point = claim[0].center
                for claim_attempt in range(1, 3):
                    self.client.click(
                        window,
                        claim_point,
                        f"claim all mail items ({claim_attempt}/2)",
                    )
                    state, window, boxes = self.wait_for_mail_claim_result()
                    if state == "backpack full":
                        print(
                            "Detected 背包已满 while claiming mail; "
                            "leaving this character untouched"
                        )
                        skip_character = True
                        break
                    if state == "mail claim reward":
                        self.click_topmost_right_confirmation(
                            window,
                            boxes,
                            "confirm mail claim",
                        )
                        self.wait_for_settled_mailbox_after_claim()
                        print("Claimed all character mail")
                        break

                    # No foreground result appeared. If the mailbox is still
                    # present and not explicitly empty, the network may have
                    # dropped the click. Retry exactly once, refreshing the
                    # button coordinate when OCR can still read it.
                    refreshed_claim = exact("领取全部物品", boxes)
                    if len(refreshed_claim) == 1:
                        claim_point = refreshed_claim[0].center
                    if (
                        claim_attempt == 1
                        and find("角色邮件", boxes)
                        and not find("未收到邮件", boxes)
                    ):
                        print(
                            "Claim-all produced no effect and the mailbox is "
                            "still unchanged; retrying once"
                        )
                        time.sleep(0.6)
                        continue

                    # A disabled claim button can remain OCR-visible when the
                    # mailbox has no attachments. After the one permitted
                    # retry, a settled foreground-free mailbox is empty.
                    print(
                        "Mail claim did not open a reward dialog after the "
                        "allowed attempt(s); treating mailbox as empty"
                    )
                    break
            else:
                print("No enabled claim-all mail button was detected")
        self.return_to_town_from_page("邮箱", "mailbox")
        return not skip_character

    def wait_for_mail_claim_result(
        self,
        timeout: float = 30,
    ) -> tuple[str, Window, list[TextBox]]:
        """Wait through claim animation until a foreground result is stable."""
        deadline = time.monotonic() + timeout
        started = time.monotonic()
        previous_confirm: tuple[float, float] | None = None
        stable_confirm_frames = 0
        stable_backpack_full_frames = 0
        settled_mailbox_frames = 0
        while time.monotonic() < deadline:
            window = self.client.find_window()
            boxes = self.client.ocr(window)

            reward_title = bool(find("获得道具", boxes))
            reward_actions = bool(find("立即使用", boxes)) and bool(
                find("穿戴", boxes)
            )
            reward_dialog = reward_title or reward_actions
            confirmations = [
                box
                for box in exact("确认", boxes)
                if 0.48 < box.center[0] < 0.76
                and 0.12 < box.center[1] < 0.48
            ]
            if reward_dialog and len(confirmations) == 1:
                center = confirmations[0].center
                if (
                    previous_confirm is not None
                    and abs(center[0] - previous_confirm[0]) < 0.015
                    and abs(center[1] - previous_confirm[1]) < 0.015
                ):
                    stable_confirm_frames += 1
                else:
                    stable_confirm_frames = 1
                previous_confirm = center
                if stable_confirm_frames >= 2:
                    print("Mail reward dialog finished rendering")
                    return "mail claim reward", window, boxes
            elif len(confirmations) == 1:
                # The confirmation button is generally rendered before (and
                # recognised more reliably than) the 获得道具 title and action
                # labels. A stable centered confirmation is sufficient proof
                # that a foreground mail-result dialog is blocking the page.
                center = confirmations[0].center
                if (
                    previous_confirm is not None
                    and abs(center[0] - previous_confirm[0]) < 0.015
                    and abs(center[1] - previous_confirm[1]) < 0.015
                ):
                    stable_confirm_frames += 1
                else:
                    stable_confirm_frames = 1
                previous_confirm = center
                if stable_confirm_frames >= 3:
                    print(
                        "Mail confirmation finished rendering "
                        "(reward title was not OCR-visible)"
                    )
                    return "mail claim reward", window, boxes
            else:
                previous_confirm = None
                stable_confirm_frames = 0

            # Foreground reward rendering takes precedence over any stale
            # 背包已满 text still OCR-visible in the mailbox behind it.
            backpack_full = [
                box
                for box in find("背包已满", boxes)
                if 0.22 < box.center[0] < 0.78
                and 0.18 < box.center[1] < 0.78
            ]
            foreground_visible = reward_dialog or bool(confirmations)
            if backpack_full and not foreground_visible:
                stable_backpack_full_frames += 1
                if (
                    time.monotonic() - started >= 3.0
                    and stable_backpack_full_frames >= 3
                ):
                    print("Backpack-full state remained stable after mail claim")
                    return "backpack full", window, boxes
            else:
                stable_backpack_full_frames = 0

            # Do not let the always-visible background mailbox win while the
            # foreground dialog is delayed or partially rendered. Prefer an
            # explicit empty-mail marker. Some game versions leave a disabled
            # 领取全部物品 label visible, so its presence is not completion
            # evidence; without 未收到邮件 we use a deliberately long grace
            # period before accepting a foreground-free mailbox.
            explicit_empty = bool(find("未收到邮件", boxes))
            if find("角色邮件", boxes) and not foreground_visible:
                settled_mailbox_frames += 1
            else:
                settled_mailbox_frames = 0
            if (
                (
                    explicit_empty
                    and time.monotonic() - started >= 5.0
                    and settled_mailbox_frames >= 3
                )
                or (
                    time.monotonic() - started >= 18.0
                    and settled_mailbox_frames >= 5
                )
            ):
                if not explicit_empty:
                    print(
                        "No mail confirmation appeared during the extended "
                        "foreground-free settling interval"
                    )
                return "no reward", window, boxes
            time.sleep(0.4)
        raise TimeoutError("Timed out waiting for the mail-claim result to finish rendering")

    def wait_for_settled_mailbox_after_claim(self, timeout: float = 15) -> None:
        """Require the reward overlay to disappear before clicking mailbox Back."""
        deadline = time.monotonic() + timeout
        stable_frames = 0
        while time.monotonic() < deadline:
            window = self.client.find_window()
            boxes = self.client.ocr(window)
            centered_confirmation = any(
                0.38 < box.center[0] < 0.76
                and 0.12 < box.center[1] < 0.55
                for box in exact("确认", boxes)
            )
            reward_visible = bool(find("获得道具", boxes)) or centered_confirmation
            if find("角色邮件", boxes) and not reward_visible:
                stable_frames += 1
                if stable_frames >= 3:
                    print("Detected settled mailbox after claim")
                    return
            else:
                stable_frames = 0
            time.sleep(0.4)
        raise TimeoutError("Mail reward overlay did not finish closing")

    def dismantle_all_available_equipment(self) -> None:
        """Repeatedly dismantle all selectable equipment, then return to town."""
        use_templates = (
            self.template_matcher is not None
            and self.template_matcher.has_all(
                self.DISMANTLE_TEMPLATE_NAMES
            )
            and not self.template_matcher.non_clickable(
                self.DISMANTLE_CLICKABLE_TEMPLATES
            )
        )
        if use_templates:
            # The town control remains a stable OCR anchor. From the moment the
            # inventory click is sent until the panel is closed, every state
            # decision below is made by fixed-position image templates.
            window, boxes = self.wait_for(
                ["委托", "背包"],
                "town before inventory",
                timeout=15,
            )
            self.click_from_boxes("背包", window, boxes, "open inventory")
            window, open_match = self.wait_for_template_any(
                ("dismantle_open",),
                "normal inventory dismantle button",
                timeout=15,
                stable_frames=2,
            )
            self.click_template(window, open_match, "open dismantle panel")
            self.wait_for_template_any(
                ("dismantle_empty", "dismantle_ready"),
                "inventory dismantle panel",
                timeout=15,
                stable_frames=2,
            )
            print("Using fixed-position image templates for dismantle workflow")
            self.dismantle_all_available_equipment_by_template()
            return

        self.click_then_wait(
            "背包",
            "town before inventory",
            ["委托", "背包"],
            {"inventory": ["背包", "道具"]},
        )
        if self.template_matcher is not None:
            missing = ", ".join(
                self.template_matcher.missing(self.DISMANTLE_TEMPLATE_NAMES)
            )
            non_clickable = ", ".join(
                self.template_matcher.non_clickable(
                    self.DISMANTLE_CLICKABLE_TEMPLATES
                )
            )
            issues = []
            if missing:
                issues.append(f"missing: {missing}")
            if non_clickable:
                issues.append(f"missing click points: {non_clickable}")
            print(
                f"Dismantle templates are incomplete ({'; '.join(issues)}); "
                "using OCR fallback"
            )
        self.dismantle_all_available_equipment_by_ocr()

    def dismantle_all_available_equipment_by_ocr(self) -> None:
        """Legacy dismantling path retained until all reference crops exist."""
        for batch in range(1, 21):
            window, boxes = self.wait_for(["背包", "分解"], "inventory", timeout=15)
            # The empty-state text is the authoritative stop condition. The
            # 分解 button itself remains visible even when it has no work.
            if find("没有可选择的道具", boxes):
                print("No selectable equipment remains to dismantle")
                break
            self.click_right_button("分解", window, boxes, f"dismantle batch {batch}")
            window, boxes = self.wait_for(["提示", "确认"], "dismantle confirmation", timeout=12)
            self.click_topmost_right_confirmation(window, boxes, "confirm dismantle")

            # Dismantling can show a second high-value warning and a final
            # completion acknowledgement. Confirm each visible foreground
            # prompt before inspecting the next batch.
            for confirmation in range(1, 4):
                time.sleep(0.8)
                window = self.client.find_window()
                boxes = self.client.ocr(window)
                choices = [box for box in exact("确认", boxes) if box.center[0] > 0.5]
                if not choices:
                    break
                self.client.click(
                    window,
                    max(choices, key=lambda box: box.center[1]).center,
                    f"confirm dismantle follow-up {confirmation}",
                )
            time.sleep(0.8)
        else:
            raise RuntimeError("Dismantle safety limit reached before the empty equipment state")

        # The dismantle modal has an unlabeled X in its upper-right corner.
        # Only click this fixed point after the explicit empty-state gate.
        window, boxes = self.wait_for(["没有可选择的道具"], "empty dismantle panel", timeout=10)
        self.client.click(window, (0.94, 0.895), "close empty dismantle panel")
        self.wait_for(["背包", "道具"], "inventory after dismantle close", timeout=12)
        self.return_to_town_from_page("背包", "inventory")

    def dismantle_all_available_equipment_by_template(self) -> None:
        """Dismantle using only fixed-position crops after the inventory opens."""
        for batch in range(1, 21):
            window, match = self.wait_for_template_any(
                ("dismantle_empty", "dismantle_ready"),
                "dismantle ready or empty",
                timeout=15,
                stable_frames=2,
            )
            if match.name == "dismantle_empty":
                print("Template detected no selectable equipment")
                break
            self.click_template(window, match, f"dismantle batch {batch}")

            window, prompt = self.wait_for_template_any(
                ("dismantle_confirm",),
                "dismantle confirmation",
                timeout=12,
            )
            # The first confirmation can be followed by a high-value warning
            # and a completion acknowledgement. All variants share the same
            # logical name but carry their own crop and click offset.
            for confirmation in range(1, 5):
                label = (
                    "confirm dismantle"
                    if confirmation == 1
                    else f"confirm dismantle follow-up {confirmation - 1}"
                )
                self.click_template(window, prompt, label)
                time.sleep(0.7)
                window, next_state = self.wait_for_template_any(
                    (
                        "dismantle_confirm",
                        "dismantle_empty",
                        "dismantle_ready",
                    ),
                    "dismantle prompt transition",
                    timeout=10,
                )
                if next_state.name != "dismantle_confirm":
                    # The panel can become briefly readable before the
                    # dismantle-result reward dialog finishes animating in.
                    # Give that foreground dialog a short, explicit window to
                    # appear and acknowledge it before beginning the next
                    # template-only panel wait.
                    self.dismiss_dismantle_reward_popup()
                    break
                prompt = next_state
            else:
                raise RuntimeError(
                    "Dismantle confirmation remained after four template clicks"
                )
        else:
            raise RuntimeError("Dismantle safety limit reached before the empty equipment state")

        window, close = self.wait_for_template_any(
            ("dismantle_close",),
            "empty dismantle panel close",
            timeout=10,
            stable_frames=2,
        )
        self.click_template(window, close, "close empty dismantle panel")
        window, _ = self.wait_for_template_any(
            ("dismantle_open", "inventory_ready"),
            "inventory after dismantle close",
            timeout=12,
            stable_frames=2,
        )
        self.client.click(window, self.PAGE_BACK_POINT, "back from inventory")
        self.wait_for(["委托", "选角"], "town after inventory exit", timeout=25)

    def dismiss_dismantle_reward_popup(self, timeout: float = 3.0) -> bool:
        """Acknowledge the delayed 获得道具 dialog after a dismantle batch."""
        deadline = time.monotonic() + timeout
        visible_frames = 0
        while time.monotonic() < deadline:
            window = self.client.find_window()
            boxes = self.client.ocr(window)
            if find("获得道具", boxes):
                visible_frames += 1
                choices = [
                    box
                    for box in exact("确认", boxes)
                    if 0.38 < box.center[0] < 0.68
                    and 0.20 < box.center[1] < 0.55
                ]
                if len(choices) == 1:
                    print("Detected delayed dismantle reward dialog")
                    self.client.click(
                        window,
                        choices[0].center,
                        "confirm dismantle rewards",
                    )
                    time.sleep(0.8)
                    return True
                if visible_frames >= 2:
                    # The title uniquely gates this one-button dismantle
                    # result. If Vision drops the button glyph, use its fixed
                    # centered position rather than allowing the next panel
                    # template wait to time out behind the modal.
                    print(
                        "Detected delayed dismantle reward dialog; using its "
                        "fixed centered confirmation point"
                    )
                    self.client.click(
                        window,
                        (0.52, 0.35),
                        "confirm dismantle rewards",
                    )
                    time.sleep(0.8)
                    return True
                if self.debug:
                    print(
                        "Dismantle reward dialog is visible but its centered "
                        f"确认 button is not stable yet ({len(choices)} candidate(s))"
                    )
            else:
                visible_frames = 0
            time.sleep(0.3)
        return False

    def wait_for_template_any(
        self,
        names: tuple[str, ...],
        state: str,
        timeout: float,
        stable_frames: int = 1,
    ) -> tuple[Window, TemplateMatch]:
        """Wait for the first matching template in priority order."""
        if self.template_matcher is None:
            raise RuntimeError("Fixed template matcher is not configured")
        deadline = time.monotonic() + timeout
        previous_name: str | None = None
        consecutive = 0
        while time.monotonic() < deadline:
            window = self.client.find_window()
            screenshot = self.client.capture_png_bytes(window)
            match: TemplateMatch | None = None
            for name in names:
                match = self.template_matcher.match(screenshot, name)
                if match is not None:
                    break
            if match is not None:
                if match.name == previous_name:
                    consecutive += 1
                else:
                    previous_name = match.name
                    consecutive = 1
                if consecutive >= stable_frames:
                    print(f"Template detected {state}: {match.name} ({match.score:.3f})")
                    return window, match
            else:
                previous_name = None
                consecutive = 0
            time.sleep(0.35)

        window = self.client.find_window()
        screenshot = self.client.capture_png_bytes(window)
        scores = self.template_matcher.scores(screenshot, names)
        details = ", ".join(f"{name}={scores[name]:.3f}" for name in names)
        raise TimeoutError(f"Timed out waiting for template state {state}: {details}")

    def click_template(self, window: Window, match: TemplateMatch, label: str) -> None:
        point = match.click_point_vision
        if point is None:
            raise RuntimeError(
                f"Template {match.name!r} has no click point; recapture it with --clickable"
            )
        self.client.click(window, point, f"{label} [{match.name} {match.score:.3f}]")

    def click_topmost_right_confirmation(
        self,
        window: Window,
        boxes: list[TextBox],
        label: str,
    ) -> None:
        """Click the foreground confirmation when dimmed dialogs contain another one."""
        choices = [box for box in exact("确认", boxes) if box.center[0] > 0.5]
        if not choices:
            raise RuntimeError("Could not identify an exact right-side confirmation button")
        # Vision uses a bottom-left origin; the foreground dialog's button is
        # visually higher than a dimmed dialog behind it.
        self.client.click(window, max(choices, key=lambda box: box.center[1]).center, label)

    def return_to_town_from_page(self, title: str, state: str) -> None:
        """Use a known page title as a gate before clicking that page's back arrow."""
        window, _ = self.wait_for([title], state, timeout=12)
        self.client.click(window, self.PAGE_BACK_POINT, f"back from {title}")
        self.wait_for(["委托", "选角"], "town after page exit", timeout=25)

    def wait_for_town_fatigue(self, timeout: float = 30) -> int | None:
        """Read town fatigue, using focused HUD OCR as a fallback to full-screen OCR."""
        window, boxes = self.wait_for(["委托", "选角"], "town character controls", timeout=60)
        deadline = time.monotonic() + timeout
        attempts = 0
        while True:
            fatigue = self.town_fatigue(boxes)
            if fatigue is not None:
                print(f"Detected town character fatigue: {fatigue}/100")
                return fatigue
            # The fatigue text is small and sometimes obscured by animation.
            # Restricting Vision to the top-left HUD gives it far less unrelated
            # text to confuse with a /100 value.
            hud_boxes = self.client.ocr_region(window, (0.00, 0.70, 0.35, 0.30))
            fatigue = self.town_fatigue(hud_boxes, restrict_to_town_hud=False)
            if fatigue is not None:
                print(f"Detected town character fatigue with focused HUD OCR: {fatigue}/100")
                return fatigue
            if time.monotonic() >= deadline:
                print("Could not read current character fatigue from the town HUD after focused retries")
                return None
            attempts += 1
            if attempts == 1 or attempts % 5 == 0:
                print(f"Town fatigue OCR retry {attempts}; waiting for a stable HUD frame")
            time.sleep(0.6)
            window = self.client.find_window()
            boxes = self.client.ocr(window)

    @staticmethod
    def town_fatigue(boxes: list[TextBox], restrict_to_town_hud: bool = True) -> int | None:
        readings: list[tuple[TextBox, int]] = []
        for box in boxes:
            # Vision may confuse O/0 or omit spacing around the slash. Accept
            # those harmless variants, but only in the known HUD region when
            # searching a full screen.
            normalized = box.normalized.upper().replace("O", "0").replace("I", "1")
            match = re.search(r"(?<![0-9])([0-9]{1,3})\s*[/|]?\s*100(?![0-9])", normalized)
            if (
                match
                and 0 <= int(match.group(1)) <= 100
                # Current-character HUD is in the upper-left of the town
                # window. Restricting the region avoids unrelated /100 text.
                and (
                    not restrict_to_town_hud
                    or (box.center[0] < 0.32 and box.center[1] > 0.72)
                )
            ):
                readings.append((box, int(match.group(1))))
        if not readings:
            return None
        return min(
            readings,
            key=lambda reading: (
                (reading[0].center[0] - 0.15) ** 2
                + (reading[0].center[1] - 0.88) ** 2
            ),
        )[1]

    def switch_to_available_character(
        self,
        processed: set[str] | None = None,
        reset_to_top: bool = False,
        minimum_fatigue: int = 10,
        reuse_current_if_first: bool = False,
    ) -> bool:
        """Choose the first unprocessed eligible character from the board."""
        window, boxes = self.wait_for(["委托", "选角"], "town character controls", timeout=60)
        for attempt in range(1, 4):
            # The rift-town NPC panel can remain open after dungeon exit and
            # intercept the top-left 选角 button. Returning to town is a safe
            # way to dismiss it before we retry the intended action.
            if find("秘境传送口", boxes) and exact("返回城镇", boxes):
                print("Detected open 秘境传送口 panel; returning to town before character selection")
                self.click_right_button("返回城镇", window, boxes, "dismiss rift-town panel")
                time.sleep(2)
                window, boxes = self.wait_for(["委托", "选角"], "town after rift-panel dismissal", timeout=20)
                continue
            self.client.click(
                window,
                self.CHARACTER_SELECT_POINT,
                f"character selection ({attempt}/3)",
            )
            try:
                window, boxes = self.wait_for_character_selection_board(timeout=12)
                print("Character selection board is ready")
                break
            except TimeoutError:
                # The click may have been intercepted. Only retry after town
                # controls are positively visible; if a late board render
                # appears, recognise it before making another click.
                try:
                    window, boxes = self.wait_for_character_selection_board(timeout=3)
                    print("Character selection board finished rendering")
                    break
                except TimeoutError:
                    window, boxes = self.wait_for(
                        ["委托", "选角"],
                        "town character controls",
                        timeout=8,
                    )
                print(f"Character selection did not open; retrying ({attempt}/3)")
        else:
            print("Character selection did not open after three safe recovery attempts")
            return False

        if reset_to_top or (
            processed is None and not self.character_traversal_started
        ):
            window, boxes = self.scroll_character_board_to_top(window, boxes)

        board_deadline = time.monotonic() + 90
        stable_empty_frames = 0
        unchanged_scrolls = 0
        scrolls = 0
        # Once the current 在线 row has been positively located, every later
        # scrolled view is necessarily below it. Keep that fact even after the
        # marker itself moves off-screen.
        passed_online_row = not bool(processed)
        while True:
            frame = self.client.capture_png_bytes(window)
            rows = self.character_board_calibrated_rows(window, frame)
            if self.debug:
                if rows:
                    values = ", ".join(
                        f"slot {index + 1}: level={row.level}, "
                        f"fatigue={row.fatigue}, online={row.online}, "
                        f"y={row.click_y:.3f}"
                        for index, row in enumerate(rows)
                    )
                    print(f"Calibrated character-board rows: {values}")
                else:
                    print("No complete character-board row borders detected")
            available = [
                row
                for row in rows
                if row.level is not None
                and row.fatigue is not None
                and row.level >= 75
                and row.fatigue >= minimum_fatigue
            ]
            initial_prefix_unreadable = False
            if not processed and available:
                # At the initial top-of-list scan, never treat the current
                # 在线 row as the first eligible character merely because OCR
                # missed a preceding row's level or fatigue. Every complete
                # row above the candidate must be conclusively readable; an
                # unreadable predecessor causes a safe in-place retry.
                candidate_index = rows.index(available[0])
                unreadable_before = [
                    row
                    for row in rows[:candidate_index]
                    if row.level is None or row.fatigue is None
                ]
                if unreadable_before:
                    initial_prefix_unreadable = True
                    if self.debug:
                        print(
                            "A row above the first detected eligible character "
                            "is not fully readable; retrying before selecting "
                            "or reusing the 在线 role"
                        )
                    available = []
            elif not processed:
                initial_prefix_unreadable = any(
                    row.level is None or row.fatigue is None
                    for row in rows
                )
            if processed:
                online_rows = [row for row in rows if row.online]
                if len(online_rows) == 1:
                    online = online_rows[0]
                    passed_online_row = True
                    available = [
                        row
                        for row in available
                        if row.top >= online.bottom - 0.01
                    ]
                    if self.debug:
                        print(
                            f"Continuing below calibrated online slot at "
                            f"y={online.click_y:.3f}; {len(available)} "
                            "eligible row(s) remain"
                        )
                elif not passed_online_row:
                    available = []
                    if self.debug:
                        print(
                            "Calibrated 在线 slot is not readable yet; "
                            "waiting without clicking or scrolling"
                        )
            if available:
                selected = available[0]
                if (
                    (reuse_current_if_first or processed is None)
                    and not processed
                    and selected.online
                ):
                    print(
                        f"Current character is the first eligible role "
                        f"(level {selected.level}); processing it without re-login"
                    )
                    if processed is not None:
                        processed.add(f"role-{len(processed) + 1}")
                    self.character_traversal_started = True
                    self.client.click(
                        window,
                        self.CHARACTER_BOARD_CLOSE_POINT,
                        "close character selection board",
                    )
                    self.wait_for(["委托", "选角"], "town after character-board close", timeout=15)
                    return True
                print(
                    f"Selecting first calibrated row with level {selected.level}, "
                    f"{selected.fatigue}/100 fatigue"
                )
                if processed is not None:
                    processed.add(f"role-{len(processed) + 1}")
                self.client.click(
                    window,
                    (0.45, selected.click_y),
                    f"available character ({selected.fatigue}/100)",
                )
                time.sleep(0.8)
                window = self.client.find_window()
                boxes = self.client.ocr(window)
                self.click_right_button("开始游戏", window, boxes, "start selected character")
                self.wait_for(
                    ["委托", "选角"],
                    "new character logged into town",
                    timeout=90,
                )
                self.character_traversal_started = True
                print("New character login complete")
                return True

            if initial_prefix_unreadable:
                if time.monotonic() >= board_deadline:
                    print(
                        "Timed out reading the top character rows; the current "
                        "在线 role was not reused because an earlier row could "
                        "not be ruled out"
                    )
                    return False
                stable_empty_frames = 0
                time.sleep(0.5)
                window = self.client.find_window()
                boxes = self.client.ocr(window)
                continue

            if processed and not passed_online_row:
                if time.monotonic() >= board_deadline:
                    print(
                        "Timed out locating the current 在线 row; no character "
                        "was selected because board order could not be proven"
                    )
                    return False
                # Scrolling before finding the marker destroys the only
                # reliable ordering anchor. Re-render and retry in place.
                stable_empty_frames = 0
                time.sleep(0.5)
                window = self.client.find_window()
                boxes = self.client.ocr(window)
                continue

            # Require two frames with at least three complete geometric slots
            # before scrolling. OCR values can be absent without changing the
            # panel geometry, so unreadable text never defines row position.
            if len(rows) >= 3:
                stable_empty_frames += 1
            else:
                stable_empty_frames = 0
            if stable_empty_frames >= 2:
                if scrolls >= 30:
                    print("Stopped character-board search after 30 downward scrolls without an eligible role")
                    return False
                before = self.client.capture_png_bytes(window)
                scrolls += 1
                print(
                    f"No eligible calibrated row on this view; scrolling "
                    f"exactly one row down ({scrolls})"
                )
                # Vision y increases upward. This physical upward drag is one
                # calibrated 214px row at the reference window height.
                self.client.drag(
                    window,
                    start=(0.50, 0.35),
                    end=(0.50, 0.35 + self.CHARACTER_BOARD_ROW_PITCH),
                    label="character list downward",
                )
                time.sleep(0.7)
                window = self.client.find_window()
                boxes = self.client.ocr(window)
                after = self.client.capture_png_bytes(window)
                difference = self.client.region_difference(
                    before,
                    after,
                    self.CHARACTER_BOARD_PANEL,
                )
                if difference is not None and difference < 2.5:
                    unchanged_scrolls += 1
                    print(f"Character list did not move (difference {difference:.2f}; {unchanged_scrolls}/2)")
                else:
                    unchanged_scrolls = 0
                    if difference is None:
                        print("Character-list movement could not be measured; continuing with OCR")
                    else:
                        print(f"Character list moved (difference {difference:.2f})")
                if unchanged_scrolls >= 2:
                    print("Reached the bottom of the character list; no eligible role remains")
                    return False
                stable_empty_frames = 0
                continue
            if time.monotonic() >= board_deadline:
                print(
                    "Timed out reading an eligible role while searching the character board "
                    f"(last frame contained {len(rows)} complete row slot(s))"
                )
                return False
            time.sleep(0.5)
            window = self.client.find_window()
            boxes = self.client.ocr(window)

    @classmethod
    def character_board_row_regions(
        cls,
        png_bytes: bytes,
    ) -> list[tuple[float, float]]:
        """Find complete row borders inside the calibrated rolling panel.

        Rows have a fixed 214px pitch at the reference resolution. Only their
        phase changes when the final scroll is clamped. Horizontal border
        energy determines that phase; spans shorter than a full row are
        discarded as clipped rows.
        """
        try:
            import cv2
            import numpy as np
        except ImportError as error:
            raise RuntimeError(
                "OpenCV and NumPy are required for calibrated character rows"
            ) from error
        frame = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            raise ValueError("Could not decode character-board screenshot")
        height, width = frame.shape
        left, panel_top, right, panel_bottom = cls.CHARACTER_BOARD_PANEL
        x0, x1 = round(left * width), round(right * width)
        y0, y1 = round(panel_top * height), round(panel_bottom * height)
        pitch = max(20, round(cls.CHARACTER_BOARD_ROW_PITCH * height))
        strip = frame[y0:y1, x0:x1].astype("float32")
        profile = np.abs(np.diff(strip.mean(axis=1)))
        radius = max(4, round(pitch * 0.045))

        best_phase = y0
        best_score = -1.0
        # One pitch contains every possible clamped-scroll phase.
        for phase in range(y0, min(y1, y0 + pitch)):
            peaks: list[float] = []
            expected = phase
            while expected <= y1:
                local = expected - y0
                start = max(0, local - radius)
                end = min(len(profile), local + radius + 1)
                if end > start:
                    peaks.append(float(profile[start:end].max()))
                expected += pitch
            if len(peaks) >= 3:
                # Strong repeated borders matter more than a single header or
                # footer edge. The median suppresses animated/text outliers.
                score = float(np.median(peaks)) + 0.2 * float(np.mean(peaks))
                if score > best_score:
                    best_score = score
                    best_phase = phase

        boundaries: list[int] = []
        expected = best_phase
        while expected <= y1 + radius:
            local = expected - y0
            start = max(0, local - radius)
            end = min(len(profile), local + radius + 1)
            if end > start:
                boundary = y0 + start + int(np.argmax(profile[start:end]))
                if not boundaries or boundary - boundaries[-1] > pitch * 0.5:
                    boundaries.append(boundary)
            expected += pitch

        rows: list[tuple[float, float]] = []
        for top, bottom in zip(boundaries, boundaries[1:]):
            span = bottom - top
            if 0.82 * pitch <= span <= 1.18 * pitch:
                rows.append((top / height, bottom / height))
        return rows

    def character_board_calibrated_rows(
        self,
        window: Window,
        png_bytes: bytes,
    ) -> list[CharacterBoardRow]:
        """Read level/fatigue only from fixed subregions of complete rows."""
        regions = self.character_board_row_regions(png_bytes)
        if not regions:
            return []
        # Vision needs surrounding context to recognize isolated glyphs
        # reliably, so each OCR request includes padding. Values are accepted
        # only when their final full-window coordinates fall in the narrower
        # calibrated strips below.
        padded_left_region = (0.05, 0.15, 0.21, 0.69)
        padded_fatigue_region = (0.24, 0.15, 0.20, 0.69)
        level_boxes = self.client.ocr_region(
            window,
            padded_left_region,
            language_correction=False,
        )
        fatigue_boxes = self.client.ocr_region(
            window,
            padded_fatigue_region,
            language_correction=False,
        )
        try:
            import cv2
            import numpy as np
        except ImportError as error:
            raise RuntimeError(
                "OpenCV and NumPy are required for the 在线 marker"
            ) from error
        frame = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Could not decode character-board screenshot")
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        frame_height, frame_width = frame.shape[:2]

        result: list[CharacterBoardRow] = []
        for top, bottom in regions:
            height = bottom - top

            def local_position(box: TextBox) -> float:
                return (1.0 - box.center[1] - top) / height

            levels: list[int] = []
            for box in level_boxes:
                if not (
                    self.CHARACTER_BOARD_LEVEL_STRIP[0]
                    <= box.center[0]
                    <= self.CHARACTER_BOARD_LEVEL_STRIP[1]
                    and 0.30 <= local_position(box) <= 0.96
                ):
                    continue
                match = re.fullmatch(r"[^0-9]*([0-9]{1,3})[^0-9]*", box.normalized)
                if match and 1 <= int(match.group(1)) <= 100:
                    levels.append(int(match.group(1)))

            fatigues: list[int] = []
            for box in fatigue_boxes:
                if not (
                    self.CHARACTER_BOARD_FATIGUE_STRIP[0]
                    <= box.center[0]
                    <= self.CHARACTER_BOARD_FATIGUE_STRIP[1]
                    and 0.10 <= local_position(box) <= 0.72
                ):
                    continue
                normalized = box.normalized.upper().replace("O", "0").replace("I", "1")
                match = re.fullmatch(r"([0-9]{1,3})[/|]100", normalized)
                if match and 0 <= int(match.group(1)) <= 100:
                    fatigues.append(int(match.group(1)))

            online_x0 = round(
                self.CHARACTER_BOARD_ONLINE_STRIP[0] * frame_width
            )
            online_x1 = round(
                self.CHARACTER_BOARD_ONLINE_STRIP[1] * frame_width
            )
            online_y0 = round(top * frame_height)
            online_y1 = round((top + 0.38 * height) * frame_height)
            marker_crop = hsv[online_y0:online_y1, online_x0:online_x1]
            green = cv2.inRange(
                marker_crop,
                np.array([35, 90, 70], dtype=np.uint8),
                np.array([90, 255, 255], dtype=np.uint8),
            )
            green_ratio = (
                float((green > 0).mean())
                if green.size
                else 0.0
            )
            # Calibration samples: online=0.241; every other row <=0.002.
            online = green_ratio >= 0.08
            # A focused Vision pass can occasionally emit both 8 and 80 at
            # nearly the same badge. Prefer the complete larger reading.
            level = max(levels, default=None)
            fatigue = fatigues[0] if len(set(fatigues)) == 1 else None
            result.append(
                CharacterBoardRow(
                    top=top,
                    bottom=bottom,
                    click_y=1.0 - (top + bottom) / 2,
                    level=level,
                    fatigue=fatigue,
                    online=online,
                )
            )
        return result

    def scroll_character_board_to_top(
        self,
        window: Window,
        boxes: list[TextBox],
    ) -> tuple[Window, list[TextBox]]:
        """Reset the character board so maintenance always scans top-to-bottom."""
        unchanged = 0
        for attempt in range(1, 31):
            before = self.client.capture_png_bytes(window)
            print(f"Resetting character list to top ({attempt})")
            # Vision's y axis starts at the bottom. This is a physical
            # downward drag, which scrolls the list upward toward its top.
            self.client.drag(
                window,
                start=(0.50, 0.35 + self.CHARACTER_BOARD_ROW_PITCH),
                end=(0.50, 0.35),
                label="character list upward",
            )
            time.sleep(0.6)
            window = self.client.find_window()
            boxes = self.client.ocr(window)
            after = self.client.capture_png_bytes(window)
            difference = self.client.region_difference(
                before,
                after,
                self.CHARACTER_BOARD_PANEL,
            )
            if difference is not None and difference < 2.5:
                unchanged += 1
                if unchanged >= 2:
                    print("Character list is at the top")
                    break
            else:
                unchanged = 0
        return window, boxes

    def continue_from_realm_selection(self, battle: bool = False) -> bool:
        self.click_then_wait(
            "普通秘境",
            "realm selection",
            ["普通秘境", "时空秘境"],
            {"party setup": ["普通秘境", "入场材料"]},
        )
        if not self.configure_party():
            return False
        if battle:
            self.run_battle(start_by_entering=True)
        else:
            print("Party setup complete. Use --battle to enter the dungeon automatically.")
        return True

    def wait_for_realm_selection(self, timeout: float) -> str:
        # The three cards can vary by game version/account.  Any two stable
        # labels identify this screen; separate dictionary keys are required
        # here so Python does not discard alternatives with duplicate keys.
        self.wait_for_any(
            {
                "realm selection (normal)": ["时空秘境", "普通秘境"],
                "realm selection (multiple)": ["时空秘境", "多重秘境"],
                "realm selection (four-dimensional)": ["时空秘境", "四维秘境"],
            },
            timeout=timeout,
        )
        return "realm selection"

    def open_rift_commission(self) -> str:
        next_states = {
            "travel confirmation": ["提示", "时空秘境城镇"],
            "realm selection (normal)": ["时空秘境", "普通秘境"],
            "realm selection (multiple)": ["时空秘境", "多重秘境"],
            "realm selection (four-dimensional)": ["时空秘境", "四维秘境"],
            "时空秘境 town": ["时空秘境", "返回城镇"],
        }
        state, _, _ = self.click_then_wait(
            "深渊：时空秘境",
            "commission board",
            ["深渊：时空秘境"],
            next_states,
        )
        return "realm selection" if state.startswith("realm selection") else state

    def confirm_travel(self) -> str:
        window, boxes = self.wait_for(["提示", "时空秘境城镇"], "travel confirmation")
        buttons = [box for box in exact("确认", boxes) if box.center[0] > 0.5]
        if len(buttons) != 1:
            raise RuntimeError("Could not identify an exact right-side confirmation button")
        self.client.click(window, buttons[0].center, "confirmation")
        # The character may physically walk to the destination after this
        # confirmation. This is deliberately longer than UI-render waits;
        # do not re-click confirmation while travel is in progress.
        try:
            return self.wait_for_realm_selection(timeout=45)
        except TimeoutError:
            self.wait_for(["时空秘境", "返回城镇"], "时空秘境 town", timeout=5)
            return "时空秘境 town"

    def configure_party(self) -> bool:
        deadline = time.monotonic() + 8
        slot_point: tuple[float, float] | None = None
        current_fatigue: int | None = None
        while True:
            window, boxes = self.wait_for(["普通秘境"], "party setup", timeout=6)
            fatigue_readings = [
                (box, int(match.group(1)))
                for box in boxes
                if (match := re.fullmatch(r"([0-9]{1,3})/100", box.normalized))
                and 0 <= int(match.group(1)) <= 100
            ]
            if fatigue_readings:
                # The crowned current character occupies the middle card. If
                # an incomplete formation already has another card, choose
                # the fatigue label closest to that middle-card position.
                current_fatigue = min(
                    fatigue_readings,
                    key=lambda reading: abs(reading[0].center[0] - 0.68),
                )[1]
            slots = sorted(find("可配置角色", boxes), key=lambda box: box.center[0])
            party_fatigue = len(find("100/100", boxes))
            party_power = len(
                [
                    box
                    for box in boxes
                    if re.fullmatch(r"[0-9]{1,3}(?:[,，][0-9]{3})+", box.text or "")
                ]
            )
            if party_fatigue >= 3 or party_power >= 3:
                print("Detected an existing three-character formation")
                return True
            if slots:
                slot_point = (slots[0].center[0], 0.58)
                break
            if party_fatigue == 1 or party_power == 1:
                # Vision can miss the grey empty-slot text. The screen and
                # single-character state are verified before this layout click.
                slot_point = (0.49, 0.58)
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Could not determine party formation state")
            time.sleep(0.5)

        assert slot_point is not None
        if current_fatigue is not None:
            print(f"Current character fatigue: {current_fatigue}/100")
        else:
            print("Current character fatigue was not OCR-visible; using visual card order")
        for attempt in range(1, 4):
            self.client.click(window, slot_point, f"first empty party slot ({attempt}/3)")
            try:
                window, boxes = self.wait_for(
                    ["选择冒险团角色", "编队完成"],
                    "character picker",
                    timeout=8,
                )
                break
            except TimeoutError:
                self.wait_for(["普通秘境"], "party setup", timeout=3)
                print(f"Party picker did not open; retrying ({attempt}/3)")
        else:
            raise TimeoutError("Could not open party character picker")
        # The crowned main character is not labelled 选择完成. Select two
        # companions in visual order. If a visible picker page is exhausted,
        # drag its card list upward to inspect the next page; never assume all
        # possible companions fit in the initial three rows.
        companions_selected = len(find("选择完成", boxes))
        # Numeric fatigue OCR is useful for choosing a card, but explicit
        # unavailable labels are also proof that the grid has finished
        # rendering. Keep the initial grace period short so an obviously
        # exhausted page scrolls promptly.
        card_deadline = time.monotonic() + 4
        scrolls = 0
        unchanged_scrolls = 0
        # PlayCover occasionally drops a drag that starts over an animated
        # card. Cycle across different horizontal lanes and progressively
        # stronger vertical strokes before concluding the list is at bottom.
        scroll_profiles = (
            ((0.50, 0.30), (0.50, 0.30 + self.PARTY_PICKER_ROW_PITCH), 0.82),
            ((0.40, 0.30), (0.40, 0.30 + self.PARTY_PICKER_ROW_PITCH), 0.88),
            ((0.60, 0.30), (0.60, 0.30 + self.PARTY_PICKER_ROW_PITCH), 0.88),
        )
        attempted_points: set[tuple[float, float]] = set()
        # The game fills the left companion portrait before the right one.
        # Remember already occupied slots so animation in an existing portrait
        # cannot be mistaken for a newly added second companion.
        occupied_portrait_slots = set(range(min(companions_selected, 2)))
        announced_card_wait = False
        focused_view_attempted = False
        while companions_selected < 2:
            candidates = [
                candidate
                for candidate in self.eligible_cards(
                    boxes,
                    target_fatigue=current_fatigue,
                    emit_debug=False,
                )
                if candidate[1] not in attempted_points
            ]
            if not candidates and not focused_view_attempted:
                # Full-screen OCR occasionally returns a clearly visible 100
                # as 1OO/I00 or joins it to the potion icon. Retry only the
                # fixed grid, with language correction disabled, before
                # deciding this view has no usable card. Keep only numeric
                # observations so selected/blocking labels are not duplicated.
                focused_view_attempted = True
                focused = self.client.ocr_region(
                    window,
                    (0.14, 0.18, 0.74, 0.49),
                    language_correction=False,
                )
                focused_numbers = [
                    box
                    for box in focused
                    if self.party_picker_numeric_value(box) is not None
                ]
                if focused_numbers:
                    candidates = [
                        candidate
                        for candidate in self.eligible_cards(
                            [*boxes, *focused_numbers],
                            target_fatigue=current_fatigue,
                            emit_debug=False,
                        )
                        if candidate[1] not in attempted_points
                    ]
                    if candidates and self.debug:
                        print("Focused picker OCR recovered an eligible card")
            if candidates:
                fatigue, point = candidates[0]
                slot = companions_selected + 1
                print(f"Selecting visible party companion {slot} with {fatigue}/100 fatigue")
                before_click = self.client.capture_png_bytes(window)
                self.client.click(window, point, f"eligible character for companion slot {slot}")
                attempted_points.add(point)
                (
                    added,
                    window,
                    boxes,
                    changed_portrait_slot,
                ) = self.wait_for_party_companion_added(
                    window,
                    before_click,
                    companions_selected,
                    occupied_portrait_slots,
                )
                if added:
                    companions_selected += 1
                    focused_view_attempted = False
                    if changed_portrait_slot is not None:
                        occupied_portrait_slots.add(changed_portrait_slot)
                    print(
                        f"Verified companion slot {companions_selected} was added"
                    )
                else:
                    print(
                        "Card click produced no selection change; ignoring "
                        "that card and continuing the search"
                    )
                continue

            locked_tail = find("前置任务", boxes)
            fatigue_empty = find("疲劳值不足", boxes)
            if len(locked_tail) >= 4 or (
                len(locked_tail) >= 3 and len(fatigue_empty) >= 2
            ):
                if self.debug:
                    print(
                        f"Detected {len(locked_tail)} locked character cells "
                        "in the picker tail"
                    )
                print("Detected locked character rows; party picker is at the bottom")
                return self.save_party_formation(
                    window,
                    boxes,
                    companions_selected,
                    exhausted=True,
                )

            # Allow the animated cards to finish rendering before deciding that
            # their visible fatigue values are all exhausted.
            numeric_values = [
                box
                for box in boxes
                if self.party_picker_numeric_value(box) is not None
                and 0.14 < box.center[0] < 0.88
                and 0.15 < box.center[1] < 0.72
            ]
            visibly_unavailable = len(locked_tail) + len(fatigue_empty)
            if (
                len(numeric_values) < 3
                and visibly_unavailable < 3
                and time.monotonic() < card_deadline
            ):
                if not announced_card_wait:
                    print("Character cards are still rendering; waiting for readable fatigue values")
                    announced_card_wait = True
                time.sleep(0.5)
                window = self.client.find_window()
                boxes = self.client.ocr(window)
                continue

            if scrolls >= 30:
                print("No eligible party character found after 30 downward scrolls")
                return self.save_party_formation(
                    window,
                    boxes,
                    companions_selected,
                    exhausted=True,
                )
            before = self.client.capture_png_bytes(window)
            scrolls += 1
            profile = scroll_profiles[
                min(unchanged_scrolls, len(scroll_profiles) - 1)
            ]
            print(
                f"No eligible companion on this picker view; scrolling down "
                f"({scrolls}, drag profile {min(unchanged_scrolls + 1, len(scroll_profiles))}/"
                f"{len(scroll_profiles)})"
            )
            self.client.drag(
                window,
                start=profile[0],
                end=profile[1],
                label="party character list downward",
                duration=profile[2],
            )
            time.sleep(0.7)
            window = self.client.find_window()
            boxes = self.client.ocr(window)
            after = self.client.capture_png_bytes(window)
            difference = self.client.region_difference(
                before,
                after,
                self.PARTY_PICKER_PANEL,
            )
            if difference is not None and difference < 2.5:
                unchanged_scrolls += 1
                print(
                    f"Party picker did not move (difference {difference:.2f}; "
                    f"{unchanged_scrolls}/{len(scroll_profiles)})"
                )
            else:
                unchanged_scrolls = 0
                if difference is None:
                    print("Party-picker movement could not be measured; continuing with OCR")
                else:
                    print(f"Party picker moved (difference {difference:.2f})")
            if unchanged_scrolls >= len(scroll_profiles):
                self.eligible_cards(boxes, target_fatigue=current_fatigue, emit_debug=True)
                print(
                    "Party picker remained unchanged after every drag profile; "
                    "treating this as the bottom of the list"
                )
                return self.save_party_formation(
                    window,
                    boxes,
                    companions_selected,
                    exhausted=True,
                )
            # Coordinates are reused after a list scroll, so candidates from
            # the old page must not block the new page's top-left card.
            attempted_points.clear()
            focused_view_attempted = False
        return self.save_party_formation(window, boxes, companions_selected)

    def wait_for_party_companion_added(
        self,
        window: Window,
        before_click: bytes,
        previous_count: int,
        occupied_portrait_slots: set[int],
        timeout: float = 4.0,
    ) -> tuple[bool, Window, list[TextBox], int | None]:
        """Confirm that a picker click really added one companion.

        OCR marker growth is the primary signal. A persistent change in a
        previously empty top portrait slot is the fallback. Merely clicking a
        fatigue-looking card never advances the selected count.
        """
        deadline = time.monotonic() + timeout
        visual_slot: int | None = None
        stable_visual_frames = 0
        last_window = window
        last_boxes: list[TextBox] = []
        while time.monotonic() < deadline:
            time.sleep(0.35)
            last_window = self.client.find_window()
            last_boxes = self.client.ocr(last_window)
            observed = len(find("选择完成", last_boxes))
            if observed > previous_count:
                after = self.client.capture_png_bytes(last_window)
                changes = [
                    self.client.region_difference(before_click, after, region)
                    for region in self.PARTY_PICKER_COMPANION_SLOTS
                ]
                available = [
                    (change, index)
                    for index, change in enumerate(changes)
                    if index not in occupied_portrait_slots
                    and change is not None
                ]
                changed = max(available, default=(None, None))[1]
                return True, last_window, last_boxes, changed

            after = self.client.capture_png_bytes(last_window)
            changes = [
                self.client.region_difference(before_click, after, region)
                for region in self.PARTY_PICKER_COMPANION_SLOTS
            ]
            available = [
                (change, index)
                for index, change in enumerate(changes)
                if index not in occupied_portrait_slots
                and change is not None
            ]
            strongest_change, strongest_slot = max(
                available,
                default=(None, None),
            )
            if strongest_change is not None and strongest_change >= 8.0:
                if strongest_slot == visual_slot:
                    stable_visual_frames += 1
                else:
                    visual_slot = strongest_slot
                    stable_visual_frames = 1
                if stable_visual_frames >= 2:
                    if self.debug:
                        print(
                            "Selection marker was not OCR-visible; verified "
                            f"new top portrait visually (difference "
                            f"{strongest_change:.2f})"
                        )
                    return True, last_window, last_boxes, visual_slot
            else:
                visual_slot = None
                stable_visual_frames = 0
        return False, last_window, last_boxes, None

    def save_party_formation(
        self,
        window: Window,
        boxes: list[TextBox],
        companions_selected: int,
        exhausted: bool = False,
    ) -> bool:
        """Save a complete or partial formation, but never a solo formation."""
        if companions_selected == 0:
            print(
                "No eligible companion could be added; cancelling party "
                "setup and stopping before dungeon entry"
            )
            # Escape is delivered to PlayCover as Android Back and safely
            # closes the character picker without saving a solo formation.
            self.client.press(53)
            time.sleep(0.8)
            return False
        if exhausted:
            print(
                f"No further eligible companion found; entering with "
                f"{companions_selected} selected companion(s)"
            )
        complete = exact("编队完成", boxes)
        if len(complete) != 1:
            raise RuntimeError("Could not identify formation-complete button")
        for attempt in range(1, 4):
            self.client.click(window, complete[0].center, f"编队完成 ({attempt}/3)")
            time.sleep(0.8)
            # Vision occasionally misses the bright picker title for one
            # frame. Require two consecutive frames without it before
            # deciding that the modal really closed.
            picker_absent_frames = 0
            for _ in range(3):
                window = self.client.find_window()
                boxes = self.client.ocr(window)
                if find("选择冒险团角色", boxes):
                    picker_absent_frames = 0
                    break
                picker_absent_frames += 1
                if picker_absent_frames >= 2:
                    break
                time.sleep(0.4)
            if picker_absent_frames >= 2:
                print("Party formation saved")
                return True
            print(f"编队完成 did not close the picker; retrying ({attempt}/3)")
            complete = exact("编队完成", boxes)
            if len(complete) != 1:
                break
        raise RuntimeError("Party picker did not close after 编队完成")

    def run_battle(self, start_by_entering: bool = False) -> None:
        """Conservative dungeon loop; companion characters perform combat."""
        if start_by_entering:
            self.enter_dungeon()
        else:
            # Allow `battle` to resume both from an active dungeon and from a
            # completed party formation that is waiting at the 入场 button.
            window = self.client.find_window()
            boxes = self.client.ocr(window)
            if find("入场材料", boxes) and exact("入场", boxes):
                self.click_right_button("入场", window, boxes, "entry")
                _, window, boxes = self.wait_for_any(
                    {
                        "entry material confirmation": ["使用角色金库"],
                        "dungeon": ["秘境："],
                    },
                    timeout=20,
                )
                if find("使用角色金库", boxes):
                    self.click_right_button("确认", window, boxes, "entry material confirmation")
        deadline = time.monotonic() + 60 * 60
        town_frames = 0
        town_exit_expected = False
        while time.monotonic() < deadline:
            window = self.client.find_window()
            boxes = self.client.ocr(window)
            if self.dismiss_known_epic_stone_popup(window, boxes):
                town_frames = 0
                continue
            if (
                find("秘境：", boxes)
                or find("再次挑战", boxes)
                or find("领奖结算", boxes)
                or len(find("/100", boxes)) >= 2
            ):
                self.in_dungeon = True
            # Confirmation dialog after the standalone final 结算 button.
            # Check this before the dimmed background's exact 结算 text.
            if find("完成结算", boxes) and exact("确认", boxes):
                self.click_right_button("确认", window, boxes, "final settlement confirmation")
                time.sleep(2)
                continue
            # Final completion panel shown after 领奖结算. An *exact* standalone
            # 结算 is sufficient—do this before every reward/dungeon branch so
            # the background 领奖结算 can never trigger another loot sweep.
            if exact("结算", boxes):
                town_exit_expected = True
                self.click_from_boxes("结算", window, boxes, "final settlement")
                time.sleep(2)
                continue
            if find("使用角色金库", boxes):
                self.click_right_button("确认", window, boxes, "entry material confirmation")
                time.sleep(2)
                continue
            if self.confirm_dungeon_next_challenge(window, boxes):
                town_frames = 0
                time.sleep(1.2)
                continue
            # No 再次挑战 means the fatigue limit has been reached. This must
            # be handled before the generic in-dungeon movement rule, because
            # the result scene still contains 0/100 party HUD labels.
            if find("领奖结算", boxes) and not find("再次挑战", boxes):
                print("No retry available; collecting final rewards and settling")
                self.collect_visible_rewards()
                window = self.client.find_window()
                boxes = self.client.ocr(window)
                self.click_right_button("领奖结算", window, boxes, "settlement exit")
                town_exit_expected = True
                time.sleep(2)
                continue
            if find("再次挑战", boxes) and find("领奖结算", boxes):
                self.collect_visible_rewards()
                town_exit_expected = self.retry_or_exit()
                continue
            # After final settlement the exhausted party remains inside the
            # dungeon and still shows multiple /100 HUD labels. An exact
            # right-side 返回城镇 button is therefore stronger evidence than
            # the generic in-dungeon HUD and should be acted on immediately.
            dungeon_fatigues = [
                int(match.group(1))
                for box in boxes
                if (match := re.fullmatch(r"([0-9]{1,3})/100", box.normalized))
            ]
            exhausted_hud = bool(dungeon_fatigues) and max(dungeon_fatigues) < 10
            if (
                (town_exit_expected or exhausted_hud)
                and exact("返回城镇", boxes)
                and not exact("委托", boxes)
            ):
                self.click_right_button("返回城镇", window, boxes, "return to town")
                self.wait_for(
                    ["委托", "选角"],
                    "town after dungeon exit",
                    timeout=90,
                )
                self.in_dungeon = False
                print("Returned to town. Battle loop complete.")
                return
            # Check town only after every result-screen branch. A boss-room
            # notification can contain the substring 委托, so an inexact,
            # single-frame match can otherwise terminate the dungeon loop
            # before rewards are collected. Require an exact town control,
            # no dungeon/result evidence, and two consecutive OCR frames.
            dungeon_evidence = (
                bool(find("再次挑战", boxes))
                or bool(find("领奖结算", boxes))
                or bool(find("秘境：", boxes))
                or len(find("/100", boxes)) >= 2
            )
            # Both controls belong to the town HUD. Requiring the pair avoids
            # accepting a dungeon notification that happens to contain 委托.
            town_control = bool(exact("委托", boxes)) and bool(
                exact("选角", boxes)
            )
            if town_control and not dungeon_evidence:
                town_frames += 1
                if town_frames >= 2:
                    self.in_dungeon = False
                    print("Town screen detected. Battle loop complete.")
                    return
                time.sleep(0.6)
                continue
            town_frames = 0
            if find("秘境：", boxes) or len(find("/100", boxes)) >= 3:
                self.client.hold([124], 0.8)  # right arrow
                time.sleep(0.4)
                continue
            time.sleep(0.8)
        raise TimeoutError("Battle safety limit reached")

    def enter_dungeon(self) -> None:
        window, boxes = self.wait_for(["入场", "入场材料"], "ready formation")
        self.click_right_button("入场", window, boxes, "entry")
        state, window, boxes = self.wait_for_any(
            {
                "entry material confirmation": ["使用角色金库"],
                "dungeon": ["秘境："],
            },
            timeout=20,
        )
        if state == "entry material confirmation":
            self.click_right_button("确认", window, boxes, "entry material confirmation")

    def confirm_dungeon_next_challenge(
        self,
        window: Window,
        boxes: list[TextBox],
    ) -> bool:
        """Accept only a locally identified next-room/challenge prompt."""
        has_next = bool(find("下一", boxes)) or bool(find("下个", boxes))
        has_progress_action = any(
            find(text, boxes)
            for text in ("开始挑战", "继续挑战", "进入", "区域", "房间")
        )
        explicit_challenge = bool(find("开始挑战", boxes)) or bool(
            find("继续挑战", boxes)
        )
        # A portal prompt can use 再次挑战 as its action wording. The
        # result screen uses the same label, but also always exposes 领奖结算;
        # excluding that pair prevents this handler from stealing the normal
        # boss-result action.
        explicit_challenge = explicit_challenge or (
            bool(find("再次挑战", boxes))
            and not bool(find("领奖结算", boxes))
        )
        if not (explicit_challenge or (has_next and has_progress_action)):
            return False
        choices = [
            box
            for box in exact("确认", boxes)
            if 0.45 < box.center[0] < 0.82
            and 0.12 < box.center[1] < 0.55
        ]
        if len(choices) != 1:
            if self.debug:
                print(
                    "Detected a next-challenge prompt but could not isolate "
                    f"one safe 确认 button (found {len(choices)})"
                )
            return False
        print("Detected next-room challenge confirmation; clicking 确认")
        self.client.click(window, choices[0].center, "confirm next dungeon challenge")
        return True

    def collect_visible_rewards(self) -> None:
        """Find a pile with the model/OCR, loosely center it, then sweep."""
        window = self.client.find_window()
        center = self.wait_for_reward_pile(timeout=2.4)
        if center is None:
            self.explore_for_rewards()
            window = self.client.find_window()
            center = self.wait_for_reward_pile(timeout=2.4)
        if center is not None:
            # The spiral covers a wide surrounding area. A pile within one
            # quarter of the screen width from the vertical centre line needs
            # no horizontal correction. For farther piles, allow up to four
            # useful moves while never chasing back after an overshoot.
            previous_direction: int | None = None
            previous_distance = abs(center[0] - 0.5)
            for _ in range(4):
                if abs(center[0] - 0.5) <= 0.25:
                    break
                direction = 124 if center[0] > 0.5 else 123  # right / left arrow
                if previous_direction is not None and direction != previous_direction:
                    print("Pile crossed the centre after repositioning; stopping correction")
                    break
                print(f"Reward pile at x={center[0]:.2f}; repositioning toward screen centre")
                self.client.hold([direction], 0.35)
                time.sleep(0.5)
                updated = self.wait_for_reward_pile(timeout=1.2)
                if updated is None:
                    break
                updated_distance = abs(updated[0] - 0.5)
                center = updated
                if updated_distance >= previous_distance - 0.015:
                    print("Pile position did not improve; stopping correction")
                    break
                previous_direction = direction
                previous_distance = updated_distance
        if center is None:
            # Do not move blindly after the full model/OCR map search.
            center = (0.58, 0.43)
            print("No model/OCR pile detected; sweeping central world area without moving")
        # A pile below this line overlaps the action/skill bar. Move the
        # character downward first so the world pile shifts into a safe part
        # of the screen instead of starting a drag on a skill button.
        if center[1] < 0.30:
            print(
                f"Reward pile is low on screen (y={center[1]:.2f}); "
                "moving down before collection"
            )
            self.client.hold([125], 0.35)  # down arrow
            time.sleep(0.55)
            moved = self.wait_for_reward_pile(timeout=1.6)
            if moved is not None:
                center = moved
            else:
                center = (center[0], 0.31)
            window = self.client.find_window()

        # One slow, wide spiral is the normal pass. If a detected pile remains
        # afterward, nudge down and retry once; this specifically recovers
        # drops that were partially hidden by the lower skill controls.
        self.client.spiral_drag(window, center, radius=0.28, turns=4.5)
        time.sleep(0.55)
        remaining = self.wait_for_reward_pile(timeout=1.6)
        if remaining is not None:
            print(
                "Reward pile remains after collection; moving down slightly "
                "and retrying once"
            )
            self.client.hold([125], 0.30)  # down arrow
            time.sleep(0.55)
            retry_center = self.wait_for_reward_pile(timeout=1.6)
            if retry_center is None:
                retry_center = (remaining[0], max(remaining[1], 0.31))
            window = self.client.find_window()
            self.client.spiral_drag(
                window,
                retry_center,
                radius=0.24,
                turns=4.0,
            )

    def wait_for_reward_pile(self, timeout: float) -> tuple[float, float] | None:
        """Require a second pile observation after a short drop-settling delay."""
        deadline = time.monotonic() + timeout
        while True:
            window = self.client.find_window()
            center = self.detect_reward_pile(window)
            if center is not None:
                print("Reward pile detected; waiting 200 ms for drops to settle")
                time.sleep(0.2)
                window = self.client.find_window()
                confirmed = self.detect_reward_pile(window)
                if confirmed is not None:
                    return confirmed
                print("Pile was not visible in the confirmation frame; continuing detection")
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.4)

    def detect_reward_pile(self, window: Window) -> tuple[float, float] | None:
        """Prefer the trained detector, then fall back to reward-specific OCR."""
        detection = self.loot_detector.detect(self.client, window)
        if detection is not None:
            center = detection.center
            # Defence in depth: keep the action decision constrained even if
            # detector-side filtering changes later. The upper screen lane is
            # reserved for temporary 获得[...] notifications and can never be
            # a valid ground-collection target.
            if 0.12 < center[0] < 0.92 and 0.18 < center[1] < 0.70:
                print(
                    f"Model detected loot pile at ({center[0]:.2f}, {center[1]:.2f}), "
                    f"confidence {detection.confidence:.2f}"
                )
                return center
            if self.debug:
                print(
                    "Ignoring model loot detection outside the playable "
                    f"drop band at ({center[0]:.2f}, {center[1]:.2f})"
                )
        return self.reward_pile_center(self.client.ocr(window))

    def explore_for_rewards(self) -> None:
        """Search right first, then left when the right camera edge is reached."""
        print("No reward cue visible; exploring right, then left if needed")
        for direction, keycode in (("right", 124), ("left", 123)):
            stationary_frames = 0
            for step in range(1, 13):
                window = self.client.find_window()
                if self.detect_reward_pile(window) is not None:
                    print(f"Found reward pile while exploring {direction}")
                    return
                before = self.client.world_phase_frame(window)
                print(f"Exploring {direction} ({step}/12)")
                self.client.hold([keycode], 0.45)
                time.sleep(0.35)
                window = self.client.find_window()
                after = self.client.world_phase_frame(window)
                shift_x, shift_y, response = self.client.phase_camera_motion(before, after)
                camera_moved = response >= 0.08 and abs(shift_x) >= 2.5
                if self.debug:
                    print(
                        f"  camera phase shift=({shift_x:.2f}, {shift_y:.2f}), "
                        f"response={response:.3f}, horizontal_motion={camera_moved}"
                    )
                if not camera_moved:
                    stationary_frames += 1
                    if stationary_frames >= 2:
                        print(
                            f"Reached {direction} camera edge "
                            f"(no coherent horizontal camera translation)"
                        )
                        break
                else:
                    stationary_frames = 0

    @staticmethod
    def reward_pile_center(boxes: list[TextBox]) -> tuple[float, float] | None:
        """Locate a drop pile from item-language markers supplied by the user."""
        reward_markers = (
            "[", "]", "【", "】", "角色绑定", "超武", "材料", "四维时空",
            "星核", "星源", "碎片", "神秘", "炉岩", "炭", "斯卡迪", "印章",
            "角色", "绑定", "稀有", "源石", "石矿", "矛盾"
        )
        labels = [
            box for box in boxes
            # Gameplay-only region: excludes the top menus, right result
            # buttons, party panel, and the lower skill bar.
            if 0.16 < box.center[0] < 0.91 and 0.20 < box.center[1] < 0.70
            and any(marker in box.normalized for marker in reward_markers)
        ]
        if not labels:
            return None
        # Item labels overlap tightly. Pick the densest small spatial group so
        # a lone unrelated matching string cannot pull the sweep off-target.
        clusters = [
            [
                other for other in labels
                if (other.center[0] - seed.center[0]) ** 2 + (other.center[1] - seed.center[1]) ** 2 < 0.035
            ]
            for seed in labels
        ]
        pile = max(clusters, key=len)
        xs = sorted(box.center[0] for box in pile)
        ys = sorted(box.center[1] for box in pile)
        middle = len(pile) // 2
        return xs[middle], ys[middle]

    def retry_or_exit(self) -> bool:
        """Retry the dungeon, returning True only when settlement was chosen."""
        for attempt in range(1, 4):
            window = self.client.find_window()
            boxes = self.client.ocr(window)
            self.click_right_button("再次挑战", window, boxes, f"retry ({attempt}/3)")
            try:
                state, window, boxes = self.wait_for_any(
                    {
                        "next dungeon": ["秘境："],
                        "entry material confirmation": ["使用角色金库"],
                        "next challenge confirmation": ["再次挑战", "确认"],
                        "start challenge confirmation": ["开始挑战", "确认"],
                    },
                    timeout=7,
                )
                if state.endswith("challenge confirmation"):
                    if self.confirm_dungeon_next_challenge(window, boxes):
                        time.sleep(1.2)
                return False
            except TimeoutError:
                window = self.client.find_window()
                boxes = self.client.ocr(window)
                fatigue = [
                    int(match.group(1))
                    for box in boxes
                    if (match := re.fullmatch(r"([0-9]{1,3})/100", box.normalized))
                ]
                if fatigue and min(fatigue) < 10:
                    self.click_right_button("领奖结算", window, boxes, "settlement exit")
                    return True
                print("Retry did not transition; sweeping boss rewards again")
                self.collect_visible_rewards()
        raise RuntimeError("Items still remain after three reward-collection sweeps")

    def click_right_button(self, text: str, window: Window, boxes: list[TextBox], label: str) -> None:
        choices = [box for box in exact(text, boxes) if box.center[0] > 0.5]
        if len(choices) != 1:
            raise RuntimeError(f"Could not identify exact right-side {text!r} button")
        self.client.click(window, choices[0].center, label)

    def eligible_cards(
        self,
        boxes: list[TextBox],
        target_fatigue: int | None = None,
        emit_debug: bool | None = None,
    ) -> list[tuple[int, tuple[float, float]]]:
        row_centers = self.PARTY_PICKER_ROW_CENTERS

        def column(x: float) -> int:
            return 0 if x < 0.39 else 1 if x < 0.65 else 2

        def row(y: float) -> int:
            # Assign every OCR observation to exactly one grid row. The rows
            # are only 0.16 apart, so independent +/- tolerances can overlap
            # and incorrectly transfer fatigue/blocking state to a neighbour.
            return min(range(len(row_centers)), key=lambda index: abs(y - row_centers[index]))

        fatigue: list[tuple[TextBox, int]] = []
        for box in boxes:
            value = self.party_picker_numeric_value(box)
            if value is not None:
                fatigue.append((box, value))
        selected = find("选择完成", boxes)
        fatigue_blocked = find("疲劳值不足", boxes)
        progression_blocked = find("前置任务", boxes)

        # The picker itself is a fixed three-column grid. OCR establishes only
        # whether a cell is selected or has usable fatigue; it does not define
        # the click coordinate. Fatigue is printed just left of each card's
        # horizontal centre. Restricting the match to that band prevents the
        # character level (also a 0-100 integer) from being mistaken for
        # fatigue.
        candidates: list[tuple[int, tuple[float, float]]] = []
        for row_index, y in enumerate(row_centers):
            for card_column, x in enumerate(self.PARTY_PICKER_COLUMN_CENTERS):
                is_selected = any(
                    column(item.center[0]) == card_column
                    and row(item.center[1]) == row_index
                    for item in selected
                )
                is_blocked = any(
                    column(item.center[0]) == card_column
                    and row(item.center[1]) == row_index
                    for item in [*fatigue_blocked, *progression_blocked]
                )
                fatigue_values = [
                    value
                    for item, value in fatigue
                    if column(item.center[0]) == card_column
                    and row(item.center[1]) == row_index
                    and x - 0.09 < item.center[0] < x - 0.01
                ]
                usable_fatigue = max(fatigue_values, default=0)
                if not is_selected and not is_blocked and usable_fatigue > 0:
                    candidates.append((usable_fatigue, (x, y + 0.035)))
        # Use deterministic visual order: top-left to right, then the next
        # row. Character names/power differ between accounts and are ignored.
        candidates = sorted(
            candidates,
            key=lambda candidate: self.card_sort_key(candidate, target_fatigue),
        )
        if emit_debug is None:
            emit_debug = self.debug
        if emit_debug:
            column_name = ("left", "middle", "right")
            print(f"Party picker debug: {len(selected)} companion(s) already selected")
            if not candidates:
                samples = ", ".join(
                    f"{value}@({item.center[0]:.3f},{item.center[1]:.3f})"
                    for item, value in fatigue
                )
                print(f"  numeric OCR samples: {samples or 'none'}")
            for index, (card_fatigue, point) in enumerate(candidates, start=1):
                card_column = column(point[0])
                row = 1 if point[1] > 0.50 else 2 if point[1] > 0.34 else 3
                difference = (
                    f", difference {abs(card_fatigue - target_fatigue)}"
                    if target_fatigue is not None
                    else ""
                )
                print(
                    f"  usable card: row {row}, {column_name[card_column]}, "
                    f"fatigue {card_fatigue}{difference} "
                    f"(normalized {point[0]:.3f}, {point[1]:.3f})"
                )
        return candidates

    @staticmethod
    def party_picker_numeric_value(box: TextBox) -> int | None:
        """Parse a short picker number despite common Vision OCR confusions."""
        normalized = (
            box.normalized.upper()
            .replace("O", "0")
            .replace("Q", "0")
            .replace("I", "1")
            .replace("L", "1")
            .replace("|", "1")
        )
        match = re.fullmatch(r"[^0-9]*([0-9]{1,3})[^0-9]*", normalized)
        if match is None:
            return None
        value = int(match.group(1))
        return value if 0 <= value <= 100 else None

    @staticmethod
    def card_sort_key(
        candidate: tuple[int, tuple[float, float]],
        target_fatigue: int | None,
    ) -> tuple[float, float]:
        _, point = candidate
        return -point[1], point[0]
