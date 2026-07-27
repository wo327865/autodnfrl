from __future__ import annotations

import re
import time

from .detector import LootPileDetector
from .macos import MacClient, TextBox, Window
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


class AutoDNF:
    def __init__(
        self,
        client: MacClient,
        debug: bool = False,
        vision_fallback: VisionAdvisor | None = None,
        vision_auto_act: bool = False,
    ) -> None:
        self.client = client
        self.debug = debug
        self.loot_detector = LootPileDetector()
        self.vision_fallback = vision_fallback
        self.vision_auto_act = vision_auto_act
        self.in_dungeon = False

    def wait_for(self, texts: list[str], state: str, timeout: float = 18) -> tuple[Window, list[TextBox]]:
        _, window, boxes = self.wait_for_any({state: texts}, timeout)
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

    def run_to_party(self, battle: bool = False) -> None:
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
        self.continue_from_realm_selection(battle)

    def run_all_characters(self) -> None:
        """Run the dungeon, then rotate through every character with fatigue."""
        round_number = 1
        while True:
            current_fatigue = self.wait_for_town_fatigue()
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
            self.run_to_party(battle=True)
            round_number += 1

    def wait_for_town_fatigue(self, timeout: float = 12) -> int:
        """Read the current character's top-left fatigue display in town."""
        window, boxes = self.wait_for(["委托", "选角"], "town character controls", timeout=60)
        deadline = time.monotonic() + timeout
        while True:
            fatigue = self.town_fatigue(boxes)
            if fatigue is not None:
                print(f"Detected town character fatigue: {fatigue}/100")
                return fatigue
            if time.monotonic() >= deadline:
                raise RuntimeError("Could not read current character fatigue from the town HUD")
            time.sleep(0.5)
            window = self.client.find_window()
            boxes = self.client.ocr(window)

    @staticmethod
    def town_fatigue(boxes: list[TextBox]) -> int | None:
        readings: list[tuple[TextBox, int]] = []
        for box in boxes:
            match = re.fullmatch(r"([0-9]{1,3})/100", box.normalized)
            if (
                match
                and 0 <= int(match.group(1)) <= 100
                # Current-character HUD is in the upper-left of the town
                # window. Restricting the region avoids unrelated /100 text.
                and box.center[0] < 0.32
                and box.center[1] > 0.72
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

    def switch_to_available_character(self) -> bool:
        """Choose another character with enough fatigue for one dungeon."""
        window, boxes = self.wait_for(["委托", "选角"], "town character controls", timeout=60)
        self.click_from_boxes("选角", window, boxes, "character selection")
        window, boxes = self.wait_for(
            ["挑战进度", "开始游戏"],
            "character selection board",
            timeout=15,
        )

        board_deadline = time.monotonic() + 8
        stable_empty_frames = 0
        while True:
            fatigue_rows = [
                (box, int(match.group(1)))
                for box in boxes
                if (match := re.fullmatch(r"([0-9]{1,3})/100", box.normalized))
                and 0 <= int(match.group(1)) <= 100
                and 0.18 < box.center[1] < 0.78
            ]
            available = sorted(
                ((fatigue, box.center[1]) for box, fatigue in fatigue_rows if fatigue >= 10),
                key=lambda item: (-item[0], -item[1]),
            )
            if available:
                fatigue, row_y = available[0]
                print(f"Selecting character with {fatigue}/100 fatigue from the first board page")
                self.client.click(window, (0.45, row_y), f"available character ({fatigue}/100)")
                time.sleep(0.8)
                window = self.client.find_window()
                boxes = self.client.ocr(window)
                self.click_right_button("开始游戏", window, boxes, "start selected character")
                self.wait_for(
                    ["委托", "选角"],
                    "new character logged into town",
                    timeout=90,
                )
                print("New character login complete")
                return True

            # The title/button can render before the rows. Require two
            # complete-looking OCR frames before concluding the first page has
            # no eligible role. Never click the board's right-page arrow.
            if len(fatigue_rows) >= 3:
                stable_empty_frames += 1
            else:
                stable_empty_frames = 0
            if stable_empty_frames >= 2:
                print("First character-board page has no role with fatigue >= 10")
                return False
            if time.monotonic() >= board_deadline:
                print(
                    "Timed out reading an eligible role on the first character-board page "
                    f"(last frame contained {len(fatigue_rows)} fatigue value(s))"
                )
                return False
            time.sleep(0.5)
            window = self.client.find_window()
            boxes = self.client.ocr(window)

    def continue_from_realm_selection(self, battle: bool = False) -> None:
        self.click_then_wait(
            "普通秘境",
            "realm selection",
            ["普通秘境", "时空秘境"],
            {"party setup": ["普通秘境", "入场材料"]},
        )
        self.configure_party()
        if battle:
            self.run_battle(start_by_entering=True)
        else:
            print("Party setup complete. Use --battle to enter the dungeon automatically.")

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

    def configure_party(self) -> None:
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
                return
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
        # The crowned main character is not labelled 选择完成.  We need two
        # companions. Vision can read the label before a click but often misses
        # its bright green post-click rendering, so it is advisory rather than
        # a required state transition.
        # The modal title and button render before the animated card contents.
        # Do not treat the first OCR frame as a complete picker: wait until
        # enough cards have a readable, usable fatigue value.
        card_deadline = time.monotonic() + 8
        enough_cards_since: float | None = None
        candidates_by_point: dict[tuple[float, float], tuple[int, tuple[float, float]]] = {}
        announced_card_wait = False
        while True:
            selected = len(find("选择完成", boxes))
            needed = max(0, 2 - selected)
            frame_candidates = self.eligible_cards(
                boxes,
                target_fatigue=current_fatigue,
                emit_debug=False,
            )
            for candidate in frame_candidates:
                candidates_by_point[candidate[1]] = candidate
            candidates = sorted(
                candidates_by_point.values(),
                key=lambda candidate: self.card_sort_key(candidate, current_fatigue),
            )

            now = time.monotonic()
            if len(candidates) >= needed:
                if enough_cards_since is None:
                    enough_cards_since = now
                # Do not immediately use the first readable row. Vision often
                # recognizes the partially covered bottom row one frame before
                # the fully visible middle row. Combine frames briefly so row
                # two wins the normal top-to-bottom ordering.
                if now - enough_cards_since >= 2:
                    break
            if now >= card_deadline:
                if len(candidates) >= needed:
                    break
                # Emit the final OCR details once, rather than flooding debug
                # output for every incomplete animation frame.
                self.eligible_cards(
                    boxes,
                    target_fatigue=current_fatigue,
                    emit_debug=True,
                )
                raise RuntimeError(
                    f"Could not locate enough visible character cards "
                    f"(needed {needed}, found {len(candidates)})"
                )
            if not announced_card_wait:
                print("Character cards are still rendering; waiting for readable fatigue values")
                announced_card_wait = True
            time.sleep(0.5)
            window = self.client.find_window()
            boxes = self.client.ocr(window)
        if self.debug:
            # Print the candidates accumulated from the settled OCR frames.
            column_name = ("left", "middle", "right")
            print(f"Party picker debug: {selected} companion(s) already selected")
            for card_fatigue, point in candidates:
                card_column = 0 if point[0] < 0.39 else 1 if point[0] < 0.65 else 2
                row = 1 if point[1] > 0.50 else 2 if point[1] > 0.34 else 3
                difference = (
                    f", difference {abs(card_fatigue - current_fatigue)}"
                    if current_fatigue is not None
                    else ""
                )
                print(
                    f"  usable card: row {row}, {column_name[card_column]}, "
                    f"fatigue {card_fatigue}{difference} "
                    f"(normalized {point[0]:.3f}, {point[1]:.3f})"
                )
        for index, (_, point) in enumerate(candidates[:needed], start=1):
            self.client.click(window, point, f"eligible character for companion slot {selected + index}")
            time.sleep(0.7)
            boxes = self.client.ocr(window)
            observed = len(find("选择完成", boxes))
            if observed < selected + index:
                print("Selection marker was not OCR-visible after click; card was fatigue-verified before clicking")
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
                return
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
        while time.monotonic() < deadline:
            window = self.client.find_window()
            boxes = self.client.ocr(window)
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
                self.click_from_boxes("结算", window, boxes, "final settlement")
                time.sleep(2)
                continue
            if find("使用角色金库", boxes):
                self.click_right_button("确认", window, boxes, "entry material confirmation")
                time.sleep(2)
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
                time.sleep(2)
                continue
            if find("再次挑战", boxes) and find("领奖结算", boxes):
                self.collect_visible_rewards()
                self.retry_or_exit()
                continue
            # After final settlement the exhausted party remains inside the
            # dungeon and still shows multiple /100 HUD labels. An exact
            # right-side 返回城镇 button is therefore stronger evidence than
            # the generic in-dungeon HUD and should be acted on immediately.
            if exact("返回城镇", boxes) and not exact("委托", boxes):
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
            town_control = bool(exact("委托", boxes))
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

    def collect_visible_rewards(self) -> None:
        """Find a pile with the model/OCR, loosely center it, then sweep."""
        window = self.client.find_window()
        center = self.wait_for_reward_pile(timeout=2.4)
        if center is None:
            self.explore_for_rewards()
            window = self.client.find_window()
            center = self.wait_for_reward_pile(timeout=2.4)
        if center is not None:
            # The spiral covers some surrounding area, but the pile still
            # needs to be reasonably central. Allow up to four useful moves
            # while never chasing it back across the screen after an overshoot.
            previous_direction: int | None = None
            previous_distance = abs(center[0] - 0.5)
            for _ in range(4):
                if 0.38 <= center[0] <= 0.62:
                    break
                direction = 124 if center[0] > 0.57 else 123  # right / left arrow
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
        for radius in (0.12, 0.18):
            self.client.spiral_drag(window, center, radius=radius, turns=3.5)
            time.sleep(0.6)

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
            print(
                f"Model detected loot pile at ({center[0]:.2f}, {center[1]:.2f}), "
                f"confidence {detection.confidence:.2f}"
            )
            return center
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
            if 0.16 < box.center[0] < 0.91 and 0.20 < box.center[1] < 0.76
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

    def retry_or_exit(self) -> None:
        for attempt in range(1, 4):
            window = self.client.find_window()
            boxes = self.client.ocr(window)
            self.click_right_button("再次挑战", window, boxes, f"retry ({attempt}/3)")
            try:
                self.wait_for_any(
                    {
                        "next dungeon": ["秘境："],
                        "entry material confirmation": ["使用角色金库"],
                    },
                    timeout=7,
                )
                return
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
                    return
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
        row_centers = (0.57, 0.41, 0.25)

        def column(x: float) -> int:
            return 0 if x < 0.39 else 1 if x < 0.65 else 2

        def row(y: float) -> int:
            # Assign every OCR observation to exactly one grid row. The rows
            # are only 0.16 apart, so independent +/- tolerances can overlap
            # and incorrectly transfer fatigue/blocking state to a neighbour.
            return min(range(len(row_centers)), key=lambda index: abs(y - row_centers[index]))

        fatigue: list[tuple[TextBox, int]] = []
        for box in boxes:
            match = re.fullmatch(r"[0-9]{1,3}", box.normalized)
            if match and 0 <= int(match.group(0)) <= 100:
                fatigue.append((box, int(match.group(0))))
        selected = find("选择完成", boxes)
        fatigue_blocked = find("疲劳值不足", boxes)

        # The picker itself is a fixed three-column grid. OCR establishes only
        # whether a cell is selected or has usable fatigue; it does not define
        # the click coordinate. Fatigue is printed just left of each card's
        # horizontal centre. Restricting the match to that band prevents the
        # character level (also a 0-100 integer) from being mistaken for
        # fatigue.
        candidates: list[tuple[int, tuple[float, float]]] = []
        for row_index, y in enumerate(row_centers):
            for card_column, x in enumerate((0.267, 0.511, 0.755)):
                is_selected = any(
                    column(item.center[0]) == card_column
                    and row(item.center[1]) == row_index
                    for item in selected
                )
                is_blocked = any(
                    column(item.center[0]) == card_column
                    and row(item.center[1]) == row_index
                    for item in fatigue_blocked
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
        # Prefer fatigue closest to the current character so the party reaches
        # its limit together. Vision's top-to-bottom, left-to-right grid order
        # remains the deterministic tie-breaker.
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
    def card_sort_key(
        candidate: tuple[int, tuple[float, float]],
        target_fatigue: int | None,
    ) -> tuple[int, float, float]:
        fatigue, point = candidate
        difference = abs(fatigue - target_fatigue) if target_fatigue is not None else 0
        return difference, -point[1], point[0]
