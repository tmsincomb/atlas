"""Generate small, verified examples for human-facing validation errors."""

from __future__ import annotations

import fnmatch
import re
from pathlib import PurePosixPath

_MAX_INTERMEDIATE = 24
_GENERIC_CANDIDATES = (
    "example",
    "file",
    "data",
    "0",
    "1",
    "A",
    "example.txt",
    "example.csv",
    "example.json",
    "example.pdf",
)


def _unique(values: list[str], limit: int = _MAX_INTERMEDIATE) -> list[str]:
    """Return values in first-seen order, bounded to keep expansion cheap."""
    return list(dict.fromkeys(values))[:limit]


def _combine(left: list[str], right: list[str]) -> list[str]:
    """Build a bounded Cartesian concatenation of two candidate lists."""
    return _unique([prefix + suffix for prefix in left for suffix in right])


class _RegexWitnessParser:
    """Generate witnesses for a useful, conservative subset of Python regexes.

    Unsupported constructs fail closed.  The public helper below verifies every
    emitted candidate with ``re.search``, so this parser can never make an
    invalid example visible to a user.
    """

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self.pos = 0

    def parse(self) -> list[str]:
        values = self._expression()
        if self.pos != len(self.pattern):
            raise ValueError("unparsed regex input")
        return values

    def _expression(self) -> list[str]:
        alternatives = self._sequence()
        while self._peek() == "|":
            self.pos += 1
            alternatives += self._sequence()
        return _unique(alternatives)

    def _sequence(self) -> list[str]:
        values = [""]
        while self.pos < len(self.pattern) and self._peek() not in (")", "|"):
            atom = self._atom()
            values = _combine(values, self._quantified(atom))
        return values

    def _atom(self) -> list[str]:
        char = self._take()
        if char in ("^", "$"):
            return [""]
        if char == "\\":
            return self._escape()
        if char == "[":
            return self._character_class()
        if char == "(":
            return self._group()
        if char == ".":
            return ["x"]
        if char in ("*", "+", "?", "{"):
            raise ValueError("quantifier without an atom")
        return [char]

    def _escape(self) -> list[str]:
        char = self._take()
        categories = {
            "d": ["0", "1"],
            "D": ["a", "_"],
            "s": [" "],
            "S": ["a", "0"],
            "w": ["a", "A", "0"],
            "W": ["-", "."],
        }
        if char in categories:
            return categories[char]
        if char in ("A", "Z", "b", "B"):
            return [""]
        if char.isdigit() or char in ("x", "u", "U", "N"):
            raise ValueError("unsupported regex escape")
        escapes = {"n": "\n", "r": "\r", "t": "\t", "f": "\f", "v": "\v"}
        return [escapes.get(char, char)]

    def _character_class(self) -> list[str]:
        negated = self._peek() == "^"
        if negated:
            self.pos += 1
        chars: list[str] = []
        while self.pos < len(self.pattern) and self._peek() != "]":
            start = self._class_character()
            if self._peek() == "-" and self._peek(1) not in ("", "]"):
                self.pos += 1
                end = self._class_character()
                chars += self._range_examples(start, end)
            else:
                chars.append(start)
        if self._take() != "]":
            raise ValueError("unterminated character class")
        if negated:
            chars = [candidate for candidate in ("a", "A", "0", "_", "-") if candidate not in chars]
        if not chars:
            raise ValueError("empty character class")
        return _unique(chars, limit=3)

    def _class_character(self) -> str:
        char = self._take()
        if char != "\\":
            return char
        values = self._escape()
        if not values or len(values[0]) != 1:
            raise ValueError("unsupported character class escape")
        return values[0]

    @staticmethod
    def _range_examples(start: str, end: str) -> list[str]:
        if len(start) != 1 or len(end) != 1 or ord(start) > ord(end):
            raise ValueError("invalid character range")
        return [chr(value) for value in range(ord(start), min(ord(end), ord(start) + 2) + 1)]

    def _group(self) -> list[str]:
        if self._peek() == "?":
            self.pos += 1
            if self._peek() == ":":
                self.pos += 1
            else:
                match = re.match(r"[aiLmsux-]+:", self.pattern[self.pos :])
                if match is None:
                    raise ValueError("unsupported regex group")
                self.pos += len(match.group(0))
        values = self._expression()
        if self._take() != ")":
            raise ValueError("unterminated regex group")
        return values

    def _quantified(self, values: list[str]) -> list[str]:
        quantifier = self._peek()
        if quantifier not in ("*", "+", "?", "{"):
            return values
        if quantifier == "*":
            self.pos += 1
            result = [*values, ""]
        elif quantifier == "+":
            self.pos += 1
            result = values
        elif quantifier == "?":
            self.pos += 1
            result = [*values, ""]
        else:
            result = self._bounded_repeat(values)
        if self._peek() == "?":  # non-greedy marker has no effect on accepted values
            self.pos += 1
        return _unique(result)

    def _bounded_repeat(self, values: list[str]) -> list[str]:
        match = re.match(r"\{(\d+)(?:,(\d*)?)?\}", self.pattern[self.pos :])
        if match is None:
            raise ValueError("invalid bounded repeat")
        self.pos += len(match.group(0))
        minimum = int(match.group(1))
        maximum_text = match.group(2)
        maximum = minimum if maximum_text is None else (int(maximum_text) if maximum_text else minimum + 1)
        counts = [minimum]
        if maximum > minimum:
            counts.append(minimum + 1)
        return _unique([candidate for count in counts for candidate in self._repeat(values, count)])

    @staticmethod
    def _repeat(values: list[str], count: int) -> list[str]:
        result = [""]
        for _ in range(count):
            result = _combine(result, values)
        return result

    def _peek(self, offset: int = 0) -> str:
        position = self.pos + offset
        return self.pattern[position] if position < len(self.pattern) else ""

    def _take(self) -> str:
        if self.pos >= len(self.pattern):
            raise ValueError("unexpected end of regex")
        char = self.pattern[self.pos]
        self.pos += 1
        return char


def regex_examples(pattern: str, limit: int = 3) -> list[str]:
    """Return up to *limit* deterministic strings verified to match *pattern*.

    Generation is intentionally best-effort.  Complex constructs such as
    lookarounds and backreferences yield fewer examples rather than risking a
    misleading suggestion.
    """
    if limit <= 0:
        return []
    compiled = re.compile(pattern)
    try:
        parsed = _RegexWitnessParser(pattern).parse()
    except ValueError:
        parsed = []

    candidates = [
        f"example{value}" if value.startswith(".") else f"example{value[1:]}"
        for value in parsed
        if value.startswith((".", "x."))
    ]
    candidates += parsed
    candidates += list(_GENERIC_CANDIDATES)

    verified = [
        value
        for value in _unique(candidates)
        if value and len(value) <= 80 and value.isprintable() and compiled.search(value) is not None
    ]
    return verified[:limit]


def _prettify_glob_candidate(value: str) -> str:
    """Replace parser placeholder names with readable path components."""
    parts = value.split("/")
    return "/".join(
        "example" if part == "x" else f"example{part[1:]}" if part.startswith(("x.", ".")) else part for part in parts
    )


def _component_glob_example(component: str) -> str | None:
    """Generate one candidate for a single path-glob component."""
    if component == "**":
        return "nested"
    if component == "*":
        return "example"
    generated = regex_examples(fnmatch.translate(component), limit=12)
    for value in generated:
        candidate = _prettify_glob_candidate(value)
        if "/" not in candidate and fnmatch.fnmatchcase(candidate, component):
            return candidate
    return None


def glob_examples(pattern: str, limit: int = 3) -> list[str]:
    """Return concrete, verified paths for a pathlib-style glob."""
    if limit <= 0:
        return []
    components = pattern.replace("\\", "/").split("/")
    generated = [_component_glob_example(component) for component in components]
    if any(component is None for component in generated):
        return []
    candidate = "/".join(component for component in generated if component is not None)
    if PurePosixPath(candidate).match(pattern):
        return [candidate]
    return []
