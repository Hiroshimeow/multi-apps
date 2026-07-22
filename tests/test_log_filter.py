import unittest

from lib.ui.log_filter import (
    FILTER_WARNING_STANDALONE_BANG,
    HighlightRange,
    LogFilterResultState,
    apply_log_filter,
    highlight_ranges,
    parse_filter_expression,
)


class LogFilterParserTests(unittest.TestCase):
    def test_empty_whitespace_brackets_and_unmatched_brackets(self):
        self.assertEqual(parse_filter_expression("").positive_terms, ())
        self.assertEqual(parse_filter_expression("   ").positive_terms, ())
        self.assertEqual(parse_filter_expression("[x,y,!z]").positive_terms, ("x", "y"))
        self.assertEqual(parse_filter_expression("[x,y,!z]").exclusion_terms, ("z",))
        self.assertEqual(parse_filter_expression("x,y,!z"), parse_filter_expression("[x,y,!z]"))
        self.assertEqual(parse_filter_expression("[x,y").positive_terms, ("[x", "y"))
        self.assertEqual(parse_filter_expression("x,y]").positive_terms, ("x", "y]"))
        self.assertEqual(parse_filter_expression("[]").positive_terms, ())

    def test_whitespace_empty_tokens_positive_exclusion_and_bang_warning(self):
        spec = parse_filter_expression("  a, , ! b,!, !   , c  ")
        self.assertEqual(spec.positive_terms, ("a", "c"))
        self.assertEqual(spec.exclusion_terms, ("b",))
        self.assertEqual(spec.warnings, (FILTER_WARNING_STANDALONE_BANG,))

    def test_duplicates_conflict_unicode_literals_and_commas(self):
        spec = parse_filter_expression("a,A,!B,!b,a,!a,Straße,STRASSE,.*,x,y")
        self.assertEqual(spec.positive_terms, ("a", "Straße", ".*", "x", "y"))
        self.assertEqual(spec.exclusion_terms, ("B", "a"))
        self.assertIn("a", spec.positive_terms)
        self.assertIn("a", spec.exclusion_terms)
        split = parse_filter_expression("hello,world")
        self.assertEqual(split.positive_terms, ("hello", "world"))

    def test_non_string_rejected(self):
        for value in (None, 1, b"x"):
            with self.subTest(value=value), self.assertRaises(TypeError):
                parse_filter_expression(value)


class LogFilterExecutionTests(unittest.TestCase):
    def test_exclusion_positive_only_conflict_and_counts(self):
        spec = parse_filter_expression("alpha,!drop")
        result = apply_log_filter(["alpha here", "unrelated", "DROP this"], spec)
        self.assertEqual(result.state, LogFilterResultState.READY)
        self.assertEqual(result.source_line_count, 3)
        self.assertEqual(result.excluded_line_count, 2)
        self.assertEqual(
            [(line.source_index, line.text) for line in result.lines],
            [(0, "alpha here")],
        )
        conflict = apply_log_filter(["alpha"], parse_filter_expression("alpha,!alpha"))
        self.assertEqual(conflict.state, LogFilterResultState.FULLY_FILTERED)
        self.assertEqual(conflict.lines, ())

    def test_empty_source_and_fully_filtered(self):
        empty = apply_log_filter([], parse_filter_expression("x"))
        self.assertEqual(empty.state, LogFilterResultState.EMPTY_SOURCE)
        self.assertEqual(empty.source_line_count, 0)
        all_removed = apply_log_filter(["x", "X"], parse_filter_expression("!x"))
        self.assertEqual(all_removed.state, LogFilterResultState.FULLY_FILTERED)
        self.assertEqual(all_removed.excluded_line_count, 2)

    def test_highlights_all_non_overlapping_occurrences(self):
        spec = parse_filter_expression("aba,ba")
        ranges = highlight_ranges("ababa", spec)
        self.assertEqual(ranges, (HighlightRange(0, 3, 0), HighlightRange(3, 2, 1)))
        result = apply_log_filter(["ababa"], spec)
        self.assertEqual(result.lines[0].highlights, ranges)

    def test_same_start_tie_uses_term_order_and_overlaps_are_sorted(self):
        first = highlight_ranges("foobar", parse_filter_expression("foo,foobar"))
        self.assertEqual(first, (HighlightRange(0, 3, 0),))
        second = highlight_ranges("foobar", parse_filter_expression("foobar,foo"))
        self.assertEqual(second, (HighlightRange(0, 6, 0),))
        overlap = highlight_ranges("aaaa", parse_filter_expression("aa,a"))
        self.assertEqual(overlap, (HighlightRange(0, 2, 0), HighlightRange(2, 2, 0)))

    def test_unicode_casefold_maps_back_to_original_indices(self):
        spec = parse_filter_expression("ss")
        ranges = highlight_ranges("Straße SS", spec)
        self.assertEqual(ranges[0], HighlightRange(4, 1, 0))
        self.assertEqual(ranges[1], HighlightRange(7, 2, 0))
        self.assertTrue(all(r.start + r.length <= len("Straße SS") for r in ranges))
        full_word = highlight_ranges("Straße", parse_filter_expression("STRASSE"))
        self.assertEqual(full_word, (HighlightRange(0, 6, 0),))

    def test_text_is_preserved_and_no_html_is_generated(self):
        source = ["<b>alpha</b> & beta", "日本語 ALPHA"]
        result = apply_log_filter(source, parse_filter_expression("alpha"))
        self.assertEqual([line.text for line in result.lines], source)
        self.assertEqual(result.lines[0].text, "<b>alpha</b> & beta")

    def test_lines_must_be_strings(self):
        with self.assertRaises(TypeError):
            highlight_ranges(1, parse_filter_expression("x"))
        with self.assertRaises(TypeError):
            apply_log_filter(["ok", 1], parse_filter_expression("x"))


if __name__ == "__main__":
    unittest.main()
