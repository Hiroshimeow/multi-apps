from dataclasses import dataclass


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
