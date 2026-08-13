from pathlib import Path

import pytest

from songdrop.exceptions import FileWriteFailed
from songdrop.utils.filenames import ensure_within, sanitize_component


def test_sanitize_replaces_cross_platform_illegal_characters() -> None:
    assert sanitize_component('A/B\\C:D*E?F"G<H>I|J') == "A_B_C_D_E_F_G_H_I_J"


@pytest.mark.parametrize("value", [".", "..", "   ", "///"])
def test_sanitize_never_returns_navigation_components(value: str) -> None:
    assert sanitize_component(value) == "Untitled"


def test_sanitize_handles_windows_reserved_names_and_trailing_dots() -> None:
    assert sanitize_component("CON") == "_CON"
    assert sanitize_component("track... ") == "track"


def test_ensure_within_rejects_path_traversal(tmp_path: Path) -> None:
    root = tmp_path / "library"
    with pytest.raises(FileWriteFailed, match="outside library"):
        ensure_within(root, root / ".." / "escape.mp3")


def test_ensure_within_accepts_nested_path(tmp_path: Path) -> None:
    root = tmp_path / "library"
    expected = (root / "Artist" / "Title.m4a").resolve()
    assert ensure_within(root, root / "Artist" / "Title.m4a") == expected
