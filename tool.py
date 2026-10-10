#!/usr/bin/env python3
"""Offline, read-only audit. Python 3.12+, standard library only."""
import argparse
import json
import math
import re
import stat
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

MAX_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 50000
MAX_FINDINGS = 100

class InputError(ValueError):
    """A safe, non-payload-bearing input error code."""

class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise InputError("invalid_arguments")

def read_text(path):
    p = Path(path)
    if not stat.S_ISREG(p.stat().st_mode):
        raise InputError("regular_file_required")
    with p.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise InputError("file_size_limit")
    return data.decode("utf-8")

def reject_constant(value):
    raise InputError("non_finite_json_number")

def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError("duplicate_json_member")
        result[key] = value
    return result

def parse_json(text):
    try:
        result = json.loads(text, object_pairs_hook=unique_pairs,
                            parse_float=Decimal, parse_constant=reject_constant)
    except InputError:
        raise
    except (ValueError, RecursionError, InvalidOperation):
        raise InputError("invalid_json") from None
    stack, nodes = [(result, 0)], 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if depth > 128 or nodes > 200000:
            raise InputError("json_structure_limit")
        if isinstance(value, dict):
            stack.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list):
            stack.extend((v, depth + 1) for v in value)
    return result

class Report:
    def __init__(self, tool):
        self.tool, self.findings, self.count = tool, [], 0
    def add(self, code, **location):
        self.count += 1
        if len(self.findings) < MAX_FINDINGS:
            self.findings.append(dict(code=code, **location))
    def finish(self, **summary):
        return {"tool": self.tool, "status": "fail" if self.count else "pass",
                "summary": dict(summary, finding_count=self.count),
                "findings": self.findings,
                "findings_truncated": self.count > len(self.findings)}

def emit_main(run):
    try:
        result = run()
        code = 1 if result["status"] == "fail" else 0
    except InputError as exc:
        result, code = {"tool": TOOL, "status": "error", "error": str(exc)}, 2
    except (OSError, UnicodeError):
        result, code = {"tool": TOOL, "status": "error", "error": "unreadable_utf8_input"}, 2
    except (ValueError, RecursionError, InvalidOperation):
        result, code = {"tool": TOOL, "status": "error", "error": "invalid_input"}, 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False))
    return code

TOOL = "unicode-normalization-audit"

import unicodedata

def audit(values, *, allow_whitespace=False):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_RECORDS:
        raise InputError("nonempty_string_array_required")
    report = Report(TOOL)
    normalized_seen, raw_seen = {}, set()
    changed = controls = collisions = duplicate_rows = 0
    for number, value in enumerate(values, 1):
        if not isinstance(value, str) or len(value) > 65536:
            raise InputError("invalid_string_record")
        normalized = unicodedata.normalize("NFC", value)
        if normalized != value:
            changed += 1
            report.add("not_nfc", record=number)
        if value in raw_seen:
            duplicate_rows += 1
        raw_seen.add(value)
        first = normalized_seen.get(normalized)
        if first is not None and first[1] != value:
            collisions += 1
            report.add("nfc_collision", record=number, first_record=first[0])
        elif first is None:
            normalized_seen[normalized] = (number, value)
        for position, char in enumerate(value, 1):
            category = unicodedata.category(char)
            if category in {"Cc", "Cf", "Cs"} and not (allow_whitespace and char in "\t\n\r"):
                controls += 1
                report.add("control_or_format_character", record=number, character=position,
                           codepoint=f"U+{ord(char):04X}", category=category)
    return report.finish(records=len(values), non_nfc_records=changed,
                         collision_rows=collisions, exact_duplicate_rows=duplicate_rows,
                         control_characters=controls, unicode_version=unicodedata.unidata_version)

def run():
    parser = Parser(description=__doc__)
    parser.add_argument("input", help="JSON array of strings")
    parser.add_argument("--allow-whitespace", action="store_true")
    args = parser.parse_args()
    return audit(parse_json(read_text(args.input)), allow_whitespace=args.allow_whitespace)

if __name__ == "__main__":
    sys.exit(emit_main(run))
