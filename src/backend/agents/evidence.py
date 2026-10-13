"""Immutable-by-copy tool evidence; no aggregation or business calculations."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass


_FIELDS = ("status", "query", "data", "completeness", "provenance", "warnings")
_STATUSES = {"ok", "data_unavailable", "item_not_found", "item_alias_ambiguous", "invalid_query", "query_failed"}


@dataclass(frozen=True)
class Evidence:
    tool_name: str
    input: dict
    output: dict
    provenance: dict | list | None
    warnings: list[str]

    def as_dict(self) -> dict:
        return copy.deepcopy(dict(tool_name=self.tool_name, input=self.input, output=self.output,
                                  provenance=self.provenance, warnings=self.warnings))

    def reference(self) -> dict:
        # Existing agent_fact_references contract, without a database migration.
        return copy.deepcopy(dict(tool_name=self.tool_name, query=self.input, result=self.output))


class EvidenceCollector:
    def __init__(self):
        self._records: list[Evidence] = []

    def collect(self, tool_name: str, inputs: dict, result: object) -> Evidence:
        if not isinstance(result, Mapping) or not all(key in result for key in _FIELDS):
            raise ValueError("Incomplete tool envelope")
        if result["status"] not in _STATUSES:
            raise ValueError("Invalid tool status")
        for field in ("query", "completeness"):
            if not isinstance(result[field], dict):
                raise ValueError("Invalid structured tool output")
        if result["data"] is not None and not isinstance(result["data"], dict):
            raise ValueError("Invalid fact data")
        if result["status"] == "ok" and result["data"] is None:
            raise ValueError("Successful output lacks facts")
        if result["provenance"] is not None and not isinstance(result["provenance"], (dict, list)):
            raise ValueError("Invalid provenance")
        if not isinstance(result["warnings"], list) or not all(isinstance(w, str) for w in result["warnings"]):
            raise ValueError("Invalid warnings")
        output = copy.deepcopy({key: result[key] for key in _FIELDS})
        json.dumps(output, allow_nan=False)  # Preserve Decimal strings/null; reject non-JSON values.
        record = Evidence(tool_name, copy.deepcopy(inputs), output,
                          copy.deepcopy(output["provenance"]), copy.deepcopy(output["warnings"]))
        self._records.append(record)
        return copy.deepcopy(record)

    @property
    def records(self) -> list[Evidence]:
        return copy.deepcopy(self._records)


__all__ = ["Evidence", "EvidenceCollector"]
