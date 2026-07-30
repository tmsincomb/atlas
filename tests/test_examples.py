"""Tests for deterministic, verified diagnostic example generation."""

from __future__ import annotations

import re

import pytest

from atlas.examples import glob_examples, regex_examples


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        (r"^good\.csv$", ["good.csv"]),
        (r"^(file|dir|any)$", ["file", "dir", "any"]),
        (r"^[A-Z]{2}-[A-Z]{2}-[A-Z0-9]+$", ["AA-AA-A", "AA-AA-B", "AA-AA-C"]),
        (
            r"^.*\.(?i:jpg|jpeg|png|raw|tiff|heic|mp4|mov|pdf)$",
            ["example.jpg", "example.jpeg", "example.png"],
        ),
    ],
)
def test_regex_examples_are_deterministic_and_valid(pattern: str, expected: list[str]):
    examples = regex_examples(pattern)

    assert examples == expected
    assert all(re.search(pattern, example) is not None for example in examples)


def test_unsupported_regex_fails_closed():
    assert regex_examples(r"(?=abc)abc") == []


def test_limit_is_respected():
    assert regex_examples(r"^(file|dir|any)$", limit=2) == ["file", "dir"]
    assert regex_examples(r".*", limit=0) == []


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("*.pdf", ["example.pdf"]),
        ("dist/packages/*/bundle.js", ["dist/packages/example/bundle.js"]),
        ("outputs/**/*.json", ["outputs/nested/example.json"]),
        ("MediaLibrary/RawPhotos/*.[jJ][pP][gG]", ["MediaLibrary/RawPhotos/example.jpg"]),
    ],
)
def test_glob_examples_are_concrete_and_valid(pattern: str, expected: list[str]):
    assert glob_examples(pattern) == expected


def test_glob_examples_respect_limit():
    assert glob_examples("*.csv", limit=0) == []
