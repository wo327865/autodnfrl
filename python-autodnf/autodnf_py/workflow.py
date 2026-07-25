from __future__ import annotations

import re
import time

from .macos import MacClient, TextBox, Window


def find(text: str, boxes: list[TextBox]) -> list[TextBox]:
    target = TextBox(text, 0, 0, 0, 0).normalized
    return [box for box in boxes if target in box.normalized]


def exact(text: str, boxes: list[TextBox]) -> list[TextBox]:
    target = TextBox(text, 0, 0, 0, 0).normalized
    return [box for box in boxes if box.normalized == target]


class AutoDNF:
    def __init__(self, client: MacClient, debug: bool = False) -> None:
        self.client = client
        self.debug = debug

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
        while time.monotonic() < deadline:
            window = self.client.find_window()
            boxes = self.client.ocr(window)
            for state, texts in states.items():
                if all(find(text, boxes) for text in texts):
                    print(f"Detected {state}")
                    return state, window, boxes
            time.sleep(delay)
            delay = min(1.2, delay * 1.25)
        names = ", ".join(states)
        raise TimeoutError(f"Timed out waiting for one of: {names}")

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
        while True:
            window, boxes = self.wait_for(["普通秘境"], "party setup", timeout=6)
            slots = sorted(find("可配置角色", boxes), key=lambda box: box.center[0])
            party_fatigue = len(find("100/100", boxes))
            party_power = len([box for box in boxes if re.fullmatch(r"\d{1,3}(?:[,，]\d{3})+", box.text or "")])
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
        selected = len(find("选择完成", boxes))
        needed = max(0, 2 - selected)
        candidates = self.eligible_cards(boxes)
        if len(candidates) < needed:
            raise RuntimeError("Could not locate enough visible character cards")
        for index, (_, point) in enumerate(candidates[:needed], start=1):
            self.client.click(window, point, f"eligible character for companion slot {selected + index}")
            time.sleep(0.7)
            boxes = self.client.ocr(window)
            observed = len(find("选择完成", boxes))
            if observed < selected + index:
                print("Selection marker was not OCR-visible after click; continuing in visual grid order")
        complete = exact("编队完成", boxes)
        if len(complete) != 1:
            raise RuntimeError("Could not identify formation-complete button")
        for attempt in range(1, 4):
            self.client.click(window, complete[0].center, f"编队完成 ({attempt}/3)")
            time.sleep(0.8)
            boxes = self.client.ocr(window)
            if not find("选择冒险团角色", boxes):
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
        while time.monotonic() < deadline:
            window = self.client.find_window()
            boxes = self.client.ocr(window)
            if find("委托", boxes) or find("返回城镇", boxes):
                print("Town screen detected. Battle loop complete.")
                return
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
        """Center a reward-text cluster when necessary, then sweep it."""
        window = self.client.find_window()
        center = self.reward_pile_center(self.client.ocr(window))
        # The terms are item-specific, so a cluster near an edge is a reliable
        # cue to walk toward the pile. Stop as soon as it is near centre.
        for _ in range(3):
            if center is None or 0.43 <= center[0] <= 0.57:
                break
            direction = 124 if center[0] > 0.57 else 123  # right / left arrow
            print(f"Reward pile at x={center[0]:.2f}; repositioning toward screen centre")
            self.client.hold([direction], 0.45)
            time.sleep(0.5)
            window = self.client.find_window()
            center = self.reward_pile_center(self.client.ocr(window))
        if center is None:
            # OCR is intentionally conservative: do not move blindly if no
            # reward-specific label can be identified.
            center = (0.58, 0.43)
            print("No reward-specific label detected; sweeping central world area without moving")
        for radius in (0.12, 0.18):
            self.client.spiral_drag(window, center, radius=radius, turns=3.5)
            time.sleep(0.6)

    @staticmethod
    def reward_pile_center(boxes: list[TextBox]) -> tuple[float, float] | None:
        """Locate a drop pile from item-language markers supplied by the user."""
        reward_markers = (
            "[", "]", "【", "】", "角色绑定", "超武", "材料", "四维时空",
            "星核", "星源", "碎片", "神秘", "炉岩", "炭", "斯卡迪", "印章",
            "角色", "绑定"
        )
        labels = [
            box for box in boxes
            # Gameplay-only region: excludes the top menus, right result
            # buttons, party panel, and the lower skill bar.
            if 0.16 < box.center[0] < 0.91 and 0.20 < box.center[1] < 0.76
            and any(marker in box.text for marker in reward_markers)
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
                fatigue = [int(match.group(1)) for box in boxes if (match := re.fullmatch(r"(\d{1,3})/100", box.normalized))]
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

    def eligible_cards(self, boxes: list[TextBox]) -> list[tuple[int, tuple[float, float]]]:
        def column(x: float) -> int:
            return 0 if x < 0.39 else 1 if x < 0.65 else 2

        fatigue: list[tuple[TextBox, int]] = []
        for box in boxes:
            if box.normalized.isdigit() and 0 <= int(box.normalized) <= 100:
                fatigue.append((box, int(box.normalized)))
        selected = find("选择完成", boxes)
        fatigue_blocked = find("疲劳值不足", boxes)

        # The picker itself is a fixed three-column grid. OCR establishes only
        # whether a cell is explicitly selected or fatigue-blocked; it does
        # not define the click coordinate. This avoids duplicate/misaligned
        # rows when Vision misses a small stat label on an animated frame.
        candidates: list[tuple[int, tuple[float, float]]] = []
        for y in (0.57, 0.41, 0.25):
            for card_column, x in enumerate((0.267, 0.511, 0.755)):
                is_selected = any(
                    column(item.center[0]) == card_column and abs(item.center[1] - y) < 0.14
                    for item in selected
                )
                is_blocked = any(
                    column(item.center[0]) == card_column and abs(item.center[1] - y) < 0.16
                    for item in fatigue_blocked
                )
                if not is_selected and not is_blocked:
                    candidates.append((0, (x, y + 0.035)))
        # Vision coordinates use a bottom-left origin: a higher y value is a
        # visually higher row.  Deliberately preserve visual scan order rather
        # than ranking characters by combat power.
        candidates = sorted(candidates, key=lambda candidate: (-candidate[1][1], candidate[1][0]))
        if self.debug:
            column_name = ("left", "middle", "right")
            print(f"Party picker debug: {len(selected)} companion(s) already selected")
            for index, (_, point) in enumerate(candidates, start=1):
                card_column = column(point[0])
                row = 1 if point[1] > 0.50 else 2 if point[1] > 0.34 else 3
                print(f"  usable card: row {row}, {column_name[card_column]} (normalized {point[0]:.3f}, {point[1]:.3f})")
        return candidates
