import re
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.+?)\s*$")
LINK = re.compile(r"!?\[[^]]*\]\(([^)]+)\)")
AMBIGUOUS_PROSE = {
    "complete local capability": re.compile(
        r"\b(?:complete local (?:factory|executor|execution|lifecycle)"
        r"|local execution is complete)\b",
        re.IGNORECASE,
    ),
    "removed certification terminology": re.compile(r"\bconformance\b", re.IGNORECASE),
    "north-star workflow": re.compile(r"\bnorth[- ]star\b", re.IGNORECASE),
    "accrete knowledge": re.compile(r"\baccrete knowledge\b", re.IGNORECASE),
    "situation projection": re.compile(r"\bsituation (?:projection|contract)\b", re.IGNORECASE),
    "fail closed": re.compile(r"\bfail(?:ed|s)?[ -]closed\b", re.IGNORECASE),
    "scientific intent": re.compile(r"\bscientific intent\b", re.IGNORECASE),
}


def _documentation_paths() -> list[Path]:
    paths = [
        *ROOT.glob("*.md"),
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / "manual").rglob("*.md"),
        *(ROOT / "templates/project").glob("*.md"),
    ]
    return sorted(set(paths))


def _prose_lines(content: str) -> Iterator[tuple[int, str]]:
    fence: str | None = None
    for number, line in enumerate(content.splitlines(), start=1):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            marker_type = marker.group(1)[0]
            if fence is None:
                fence = marker_type
            elif fence == marker_type:
                fence = None
            continue
        if fence is None:
            yield number, line


def _headings(path: Path) -> list[tuple[int, int, str]]:
    headings = []
    for number, line in _prose_lines(path.read_text(encoding="utf-8")):
        match = HEADING.match(line)
        if match:
            headings.append((number, len(match.group(1)), match.group(2).rstrip(" #")))
    return headings


def _anchors(path: Path) -> set[str]:
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    for _, _, title in _headings(path):
        anchor = re.sub(r"[^\w -]", "", title.lower()).replace(" ", "-")
        count = counts.get(anchor, 0)
        counts[anchor] = count + 1
        anchors.add(anchor if count == 0 else f"{anchor}-{count}")
    return anchors


def test_documentation_has_one_h1_and_no_skipped_heading_levels():
    for path in _documentation_paths():
        headings = _headings(path)
        relative = path.relative_to(ROOT)
        assert headings, f"{relative} has no headings"
        assert sum(level == 1 for _, level, _ in headings) == 1, (
            f"{relative} must contain exactly one H1"
        )
        assert headings[0][1] == 1, f"{relative} must start its heading structure with H1"
        for previous, current in pairwise(headings):
            assert current[1] <= previous[1] + 1, (
                f"{relative}:{current[0]} skips from H{previous[1]} to H{current[1]}"
            )


def test_documentation_relative_links_and_anchors_resolve():
    for path in _documentation_paths():
        content = path.read_text(encoding="utf-8")
        for raw_target in LINK.findall(content):
            target = raw_target.strip().strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue

            destination = path if not parsed.path else path.parent / unquote(parsed.path)
            relative = path.relative_to(ROOT)
            assert destination.resolve().exists(), f"{relative}: broken link {raw_target!r}"
            if parsed.fragment:
                assert destination.suffix.lower() == ".md", (
                    f"{relative}: anchor on non-Markdown target {raw_target!r}"
                )
                assert unquote(parsed.fragment).lower() in _anchors(destination), (
                    f"{relative}: broken anchor {raw_target!r}"
                )


def test_documentation_avoids_ambiguous_internal_shorthand():
    for path in _documentation_paths():
        prose = "\n".join(line for _, line in _prose_lines(path.read_text(encoding="utf-8")))
        relative = path.relative_to(ROOT)
        for description, pattern in AMBIGUOUS_PROSE.items():
            assert not pattern.search(prose), f"{relative} uses ambiguous phrase: {description}"


def test_overview_does_not_duplicate_package_patch_version_or_removed_spec():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "0.1.0" not in readme
    assert "0.1 Alpha" in readme
    for path in _documentation_paths():
        assert "SPEC.md" not in path.read_text(encoding="utf-8"), path.relative_to(ROOT)
