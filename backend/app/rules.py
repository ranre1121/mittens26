"""Event vocabulary and rule registry, loaded from rules/*.json.

Visual detection stays separate from legal mapping: detectors emit event types,
and only this module decides which (verified) rule references a finding carries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuleRef:
    rule_id: str
    document: str
    provision: str
    url: str
    verified: bool

    def cite(self) -> str:
        mark = "" if self.verified else " [не проверено]"
        return f"{self.document}, {self.provision} ({self.url}){mark}"


@dataclass(frozen=True)
class EventDef:
    event_type: str
    label: str
    explanation: str
    rule_ids: tuple[str, ...]


class Rulebook:
    def __init__(self, rules_dir: Path, allow_unverified: bool = False):
        registry = json.loads((rules_dir / "registry.json").read_text(encoding="utf-8"))
        vocabulary = json.loads((rules_dir / "vocabulary.json").read_text(encoding="utf-8"))
        self.registry_version = registry["version"]
        self.vocabulary_version = vocabulary["version"]
        self.allow_unverified = allow_unverified
        self._documents = registry["documents"]
        self._rules = registry["rules"]
        self._events = {
            key: EventDef(key, e["label"], e["explanation"], tuple(e["rule_ids"]))
            for key, e in vocabulary["events"].items()
        }
        for event in self._events.values():
            for rule_id in event.rule_ids:
                if rule_id not in self._rules:
                    raise ValueError(f"vocabulary event {event.event_type} references unknown rule {rule_id}")

    def event(self, event_type: str) -> EventDef:
        try:
            return self._events[event_type]
        except KeyError:
            raise KeyError(f"event type {event_type!r} is not in rules/vocabulary.json") from None

    def references(self, rule_ids: tuple[str, ...] | list[str]) -> list[RuleRef]:
        refs = []
        for rule_id in rule_ids:
            rule = self._rules[rule_id]
            if not rule["verified"] and not self.allow_unverified:
                continue
            doc = self._documents[rule["document"]]
            refs.append(RuleRef(rule_id, doc["short"], rule["provision"], doc["url"], rule["verified"]))
        return refs

    @staticmethod
    def citation_text(refs: list[RuleRef]) -> str:
        if not refs:
            return "Норматив: ссылка ожидает проверки, требуется решение проверяющего."
        return "Норматив: " + "; ".join(ref.cite() for ref in refs) + "."
