from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

FILTER_WARNING_STANDALONE_BANG = "FILTER_STANDALONE_BANG_IGNORED"


@dataclass(frozen=True, slots=True)
class LogFilterSpec:
    positive_terms: tuple[str, ...] = ()
    exclusion_terms: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HighlightRange:
    start: int
    length: int
    term_index: int


class LogFilterResultState(StrEnum):
    READY = "ready"
    EMPTY_SOURCE = "empty_source"
    FULLY_FILTERED = "fully_filtered"


@dataclass(frozen=True, slots=True)
class FilteredLine:
    source_index: int
    text: str
    highlights: tuple[HighlightRange, ...] = ()


@dataclass(frozen=True, slots=True)
class LogFilterResult:
    state: LogFilterResultState
    lines: tuple[FilteredLine, ...]
    source_line_count: int
    excluded_line_count: int


def parse_filter_expression(expression: str) -> LogFilterSpec:
    if not isinstance(expression, str):
        raise TypeError("expression must be a str")

    value = expression.strip()
    if len(value) >= 2 and value.startswith("[") and value.endswith("]"):
        value = value[1:-1]

    positive = []
    exclusions = []
    positive_seen = set()
    exclusion_seen = set()
    standalone_bang = False

    for raw_token in value.split(","):
        token = raw_token.strip()
        if not token:
            continue
        if token.startswith("!"):
            term = token[1:].strip()
            if not term:
                standalone_bang = True
                continue
            folded = term.casefold()
            if folded not in exclusion_seen:
                exclusion_seen.add(folded)
                exclusions.append(term)
            continue

        folded = token.casefold()
        if folded not in positive_seen:
            positive_seen.add(folded)
            positive.append(token)

    warnings = (FILTER_WARNING_STANDALONE_BANG,) if standalone_bang else ()
    return LogFilterSpec(tuple(positive), tuple(exclusions), warnings)


def _highlight_ranges_folded(
    line: str,
    folded_line: str,
    folded_terms: tuple[str, ...],
) -> tuple[HighlightRange, ...]:
    origin_map = None
    if len(folded_line) != len(line):
        folded_parts = []
        origin_map = []
        for original_index, character in enumerate(line):
            folded = character.casefold()
            folded_parts.append(folded)
            origin_map.extend([original_index] * len(folded))
        folded_line = "".join(folded_parts)

    candidates = []
    seen = set()
    for term_index, folded_term in enumerate(folded_terms):
        if not folded_term:
            continue
        search_from = 0
        while True:
            start = folded_line.find(folded_term, search_from)
            if start < 0:
                break
            end = start + len(folded_term)
            if origin_map is None:
                candidate = (start, end, term_index)
            else:
                candidate = (
                    origin_map[start],
                    origin_map[end - 1] + 1,
                    term_index,
                )
            if candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)
            search_from = start + 1

    candidates.sort(key=lambda item: (item[0], item[2], item[1]))
    accepted = []
    previous_end = 0
    for start, end, term_index in candidates:
        if start < previous_end:
            continue
        accepted.append(HighlightRange(start, end - start, term_index))
        previous_end = end
    return tuple(accepted)


def highlight_ranges(line: str, spec: LogFilterSpec) -> tuple[HighlightRange, ...]:
    if not isinstance(line, str):
        raise TypeError("line must be a str")
    if not isinstance(spec, LogFilterSpec):
        raise TypeError("spec must be a LogFilterSpec")
    return _highlight_ranges_folded(
        line,
        line.casefold(),
        tuple(term.casefold() for term in spec.positive_terms),
    )


def apply_log_filter(lines: Sequence[str], spec: LogFilterSpec) -> LogFilterResult:
    if not isinstance(spec, LogFilterSpec):
        raise TypeError("spec must be a LogFilterSpec")
    source = tuple(lines)
    if any(not isinstance(line, str) for line in source):
        raise TypeError("all lines must be str")
    if not source:
        return LogFilterResult(LogFilterResultState.EMPTY_SOURCE, (), 0, 0)

    exclusions = tuple(term.casefold() for term in spec.exclusion_terms)
    positive_terms = tuple(term.casefold() for term in spec.positive_terms)
    kept = []
    excluded_count = 0
    for index, line in enumerate(source):
        folded_line = line.casefold()
        if any(term in folded_line for term in exclusions):
            excluded_count += 1
            continue
        kept.append(
            FilteredLine(
                index,
                line,
                _highlight_ranges_folded(line, folded_line, positive_terms),
            )
        )

    state = (
        LogFilterResultState.FULLY_FILTERED
        if not kept
        else LogFilterResultState.READY
    )
    return LogFilterResult(state, tuple(kept), len(source), excluded_count)
