"""Per-window visual checklist: Qwen states what is visible in each short time
window; deterministic code (app/pipeline/violations.py) decides from the
resulting timeline whether a violation occurred. Qwen never names violations.
"""

from __future__ import annotations

import json
from pathlib import Path

from qwen_runner.windows import strict_json

ITEMS = {
    "customer_present": "A client (not an employee) stands at the counter/window facing the cashier.",
    "multiple_customers": "Two or more different clients are being served or waiting at the counter at the same time.",
    "cashier_at_workstation": "The cashier is at their desk/workstation (seated or standing at it).",
    "cash_on_desk": "Banknotes or cash stacks lie on the desk/counter outside a closed safe, drawer or cabinet.",
    "cash_handover_in": "Cash passes from the client to the cashier.",
    "cash_handover_out": "Cash passes from the cashier to the client.",
    "cash_counting": "The cashier counts banknotes, by hand or with a counting machine.",
    "counter_reject_used": "The cashier takes a note from the counting machine's reject pocket and puts it back into the stack without checking it on a detector.",
    "safe_open": "A safe or cash cabinet door is open.",
    "key_in_safe": "A key is left inserted in a safe/cabinet lock.",
    "door_open": "The door of the cash room is open.",
    "extra_person_in_cash_area": "A person other than the cashier is inside the cashier's zone behind the counter.",
    "personal_items_on_desk": "Personal belongings on the cash desk: coat, handbag, backpack, drink, food or a personal phone.",
    "bag_opened": "A collection/cash bag is opened.",
    "bag_cash_to_safe": "Cash taken from a bag is put into a safe/cabinet.",
    "id_document_checked": "The cashier takes or looks at the client's identity document.",
    "journal_stamps_on_desk": "A cash journal or stamps lie on the desk.",
    "screen_unlocked_cashier_away": "The cashier is away while the workstation/laptop screen is on and unlocked.",
}

PROMPT = """You are a careful visual observer of top-down CCTV from a bank cashier's desk.
You receive several frames from ONE short time window, each labelled with frame_ref and timestamp_s.
For every checklist item answer only from what is visible in these frames:
  "value": true if clearly visible in at least one frame, false if clearly not happening, null if you cannot tell;
  "frame_ref": the frame_ref where it is most clearly visible (null if value is not true);
  "note": a short factual note IN RUSSIAN (at most 8 words) describing what is visible (empty string if value is not true).
Do not guess identities, do not read documents, do not judge legality. Return ONLY JSON:
{"window_id": "<id>", "items": {"<item>": {"value": true|false|null, "frame_ref": "<ref>"|null, "note": "<text>"}, ...}}"""


def messages_for(window: dict) -> list[dict]:
    from PIL import Image

    edge = int(window["image_max_edge"])
    content = []
    for frame in window["frames"]:
        image = Image.open(Path(frame["clean_path"])).convert("RGB")
        image.thumbnail((edge, edge))
        content.append({"type": "text", "text": "FRAME " + json.dumps(
            {"frame_ref": frame["frame_ref"], "timestamp_s": round(frame["timestamp_s"], 3)})})
        content.append({"type": "image", "image": image})
    content.append({"type": "text", "text": json.dumps({"window_id": window["window_id"], "checklist": ITEMS})})
    return [{"role": "system", "content": [{"type": "text", "text": PROMPT}]},
            {"role": "user", "content": content}]


def parse(raw: str, window: dict) -> dict:
    """Validated checklist row for the backend. Unknown items are ignored, missing
    ones become null; a frame_ref that was not supplied is dropped (time falls
    back to the window middle). Malformed JSON rejects the window."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    data = strict_json(text)
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        raise ValueError("Checklist items object required")
    refs = {f["frame_ref"] for f in window["frames"]}
    items = {}
    for key in ITEMS:
        row = data["items"].get(key)
        value = row.get("value") if isinstance(row, dict) else None
        if not isinstance(value, bool):
            value = None
        ref = row.get("frame_ref") if isinstance(row, dict) else None
        note = row.get("note") if isinstance(row, dict) else ""
        items[key] = {"value": value,
                      "frame_ref": ref if value and ref in refs else None,
                      "note": note[:300] if value and isinstance(note, str) else ""}
    return {"window_id": window["window_id"], "start_s": window["start_s"], "end_s": window["end_s"],
            "frame_refs": [f["frame_ref"] for f in window["frames"]], "items": items}
