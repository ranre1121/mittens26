"""Deterministic violation rules over the per-window visual checklist.

Qwen (qwen_runner.checklist) only reports what is visible in each short time
window. These rules decide, from the order and persistence of those
observations, whether a possible violation occurred. Each finding cites the
frame where the deciding observation is visible; legal references come from
rules/vocabulary.json via the Rulebook. Everything is a hint for human review.
"""

from __future__ import annotations

from typing import Callable

from app.pipeline.types import CandidateEvent

Row = dict
# A state must persist this many consecutive windows (~5 s each) to count.
STATE_WINDOWS = 2
# A door left open is common for a moment; require it for longer.
DOOR_WINDOWS = 3


def value(row: Row, key: str):
    return row["items"].get(key, {}).get("value")


def yes(key: str) -> Callable[[Row], bool]:
    return lambda row: value(row, key) is True


def runs(rows: list[Row], pred: Callable[[Row], bool]) -> list[list[Row]]:
    out, current = [], []
    for row in rows:
        if pred(row):
            current.append(row)
        elif current:
            out.append(current)
            current = []
    if current:
        out.append(current)
    return out


class Detector:
    def __init__(self, rows: list[Row], timestamps: dict[str, float]):
        self.rows = sorted(rows, key=lambda r: r["start_s"])
        self.ts = timestamps
        self.events: list[CandidateEvent] = []
        # Very short videos have only a couple of windows.
        self.min_state = 1 if len(self.rows) <= 2 else STATE_WINDOWS

    def moment(self, row: Row, key: str) -> float:
        ref = row["items"].get(key, {}).get("frame_ref")
        return self.ts.get(ref, (row["start_s"] + row["end_s"]) / 2)

    def notes(self, rows: list[Row], keys: tuple[str, ...]) -> list[str]:
        found = []
        for row in rows:
            for key in keys:
                note = row["items"].get(key, {}).get("note", "")
                if note and note not in found:
                    found.append(note.rstrip(".") + ".")
        return found[:3]

    def emit(self, event_type: str, rows: list[Row], key: str, keys: tuple[str, ...] | None = None) -> None:
        self.events.append(CandidateEvent(
            event_type=event_type, start_sec=rows[0]["start_s"], end_sec=rows[-1]["end_s"],
            representative_sec=self.moment(rows[0], key), confidence=min(0.9, 0.5 + 0.1 * len(rows)),
            observed_facts=["Наблюдение модели: " + " ".join(self.notes(rows, keys or (key,)))]
            if self.notes(rows, keys or (key,)) else [],
            visibility_limits=["Вывод сделан по выборочным кадрам; требуется проверка человеком."]))

    def state(self, event_type: str, pred: Callable[[Row], bool], key: str,
              keys: tuple[str, ...] | None = None, min_windows: int | None = None) -> None:
        need = min_windows or self.min_state
        for run in runs(self.rows, pred):
            if len(run) >= need:
                self.emit(event_type, run, key, keys)

    def customer_periods(self) -> list[list[Row]]:
        return runs(self.rows, yes("customer_present"))

    def detect(self) -> list[CandidateEvent]:
        away = lambda r: value(r, "cashier_at_workstation") is False
        self.state("safe_left_open", yes("safe_open"), "safe_open")
        self.state("safe_key_left_in_lock", yes("key_in_safe"), "key_in_safe")
        self.state("cash_left_unattended", lambda r: yes("cash_on_desk")(r) and away(r), "cash_on_desk")
        self.state("cashier_left_during_service", lambda r: yes("customer_present")(r) and away(r), "customer_present")
        self.state("personal_items_at_workplace", yes("personal_items_on_desk"), "personal_items_on_desk")
        self.state("unauthorized_person_in_cash_room", yes("extra_person_in_cash_area"), "extra_person_in_cash_area")
        self.state("cash_room_door_left_open", yes("door_open"), "door_open", min_windows=DOOR_WINDOWS)
        self.state("second_client_before_first_done", yes("multiple_customers"), "multiple_customers")
        self.state("journal_stamps_left_unattended", lambda r: yes("journal_stamps_on_desk")(r) and away(r), "journal_stamps_on_desk")
        self.state("workstation_left_unlocked", yes("screen_unlocked_cashier_away"), "screen_unlocked_cashier_away")
        self.state("rejected_banknote_unchecked", yes("counter_reject_used"), "counter_reject_used", min_windows=1)
        self.sequences()
        return self.events

    def sequences(self) -> None:
        rows = self.rows
        # Cash received, then counted after the client has gone.
        received = next((i for i, r in enumerate(rows) if value(r, "cash_handover_in") is True), None)
        if received is not None:
            late = [r for r in rows[received + 1:]
                    if value(r, "cash_counting") is True and value(r, "customer_present") is False]
            if late:
                self.emit("incoming_cash_counted_without_client", late[:1], "cash_counting")
        # Within one client's visit: cash in, counted, then cash handed back.
        for period in self.customer_periods():
            i = next((n for n, r in enumerate(period) if value(r, "cash_handover_in") is True), None)
            if i is None:
                continue
            j = next((n for n in range(i, len(period)) if value(period[n], "cash_counting") is True), None)
            if j is None:
                continue
            back = [r for r in period[j + 1:] if value(r, "cash_handover_out") is True]
            if back:
                self.emit("cash_returned_after_count", back[:1], "cash_handover_out")
        # Cash from the previous client still on the desk when the next one arrives.
        periods = self.customer_periods()
        for prev, nxt in zip(periods, periods[1:]):
            if not any(value(r, "cash_handover_in") is True for r in prev):
                continue
            for r in nxt:
                if value(r, "cash_handover_in") is True:
                    break
                if value(r, "cash_on_desk") is True:
                    self.emit("previous_client_cash_exposed", [r], "cash_on_desk")
                    break
        # A bag's cash goes into the safe without being counted in between.
        opened = next((i for i, r in enumerate(rows) if value(r, "bag_opened") is True), None)
        if opened is not None:
            for j in range(opened, len(rows)):
                if value(rows[j], "cash_counting") is True:
                    break
                if value(rows[j], "bag_cash_to_safe") is True:
                    self.emit("bag_cash_stored_uncounted", [rows[j]], "bag_cash_to_safe")
                    break


def detect(rows: list[Row], timestamps: dict[str, float]) -> list[CandidateEvent]:
    return Detector(rows, timestamps).detect()
