from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from dicepp_data import (
    QueryNormalizationLimits,
    QueryNormalizationRedirect,
    QueryNormalizationRow,
    legacy_v1_command_split,
    normalize_query_database,
)


def _row(
    rowid: int,
    name: str,
    content: str,
    *,
    english: str = "",
    source: str = "",
    category: str = "",
    tag: str = "",
) -> dict[str, object]:
    return {
        "rowid": rowid,
        "name": name,
        "english": english,
        "source": source,
        "category": category,
        "tag": tag,
        "content": content,
    }


def _codes(report) -> list[str]:
    return [issue.code for issue in report.issues]


def test_rows_are_trimmed_ordered_deduplicated_and_immutable() -> None:
    report = normalize_query_database(
        [
            _row(4, "", "orphan"),
            _row(3, " Alpha ", "conflicting", source=" Core "),
            _row(2, "Beta", "beta body"),
            _row(1, " Alpha ", "first body", english="A", source=" Core "),
        ]
    )

    assert report.rows == (
        QueryNormalizationRow(1, "Alpha", "A", "Core", "first body"),
        QueryNormalizationRow(2, "Beta", "", "", "beta body"),
    )
    assert _codes(report) == [
        "duplicate_content_conflict",
        "missing_required_field",
    ]
    assert report.issues[0].rowid == 3
    assert report.issues[0].related_rowids == (1,)
    assert report.counts.data_input == 4
    assert report.counts.data_output == 2
    assert report.counts.data_duplicates == 1
    assert report.counts.data_invalid == 1
    with pytest.raises(FrozenInstanceError):
        report.rows[0].name = "changed"  # type: ignore[misc]


def test_legacy_split_and_list_rendering_preserve_v1_behavior() -> None:
    assert legacy_v1_command_split('#book "exact phrase" word &magic') == (
        "#book",
        "exact phrase",
        "word",
        "&magic",
    )
    report = normalize_query_database(
        [
            _row(1, "parent", " /alp\n/alp|clear alpha\n/alp"),
            _row(2, "alpha", "first"),
            _row(3, "alphabet", "second"),
        ]
    )

    assert report.rows[0].content == (
        " /alp\n[ 0., 1.bet ]\n[ 2.alpha, 3.alphabet ]"
    )
    assert _codes(report) == [
        "interactive_selection_removed",
        "interactive_selection_removed",
    ]
    assert report.counts.directives_seen == 2
    assert report.counts.directives_expanded == 2


def test_show_supports_filters_redirects_modifier_order_and_no_recursion() -> None:
    report = normalize_query_database(
        [
            _row(
                1,
                "parent",
                "/spell #phb/other &magic|show 5|clear fire\n"
                "/spell #phb &magic|clear fire|show 5\n"
                "/alias|show",
            ),
            _row(
                2,
                "spell",
                "fire\nball",
                source="PHB",
                category="magic",
            ),
            _row(3, "target", "line one\n/nested remains"),
            _row(4, "target appendix", "must not be expanded by redirect"),
        ],
        [{"rowid": 1, "alias": "alias", "target": "target"}],
    )

    assert report.rows[0].content == (
        "0.spell : ...\n"
        "1.spell : fire...\n"
        "2.target : line one /nested remains"
    )
    assert "interactive_selection_removed" not in _codes(report)
    expanded = [issue for issue in report.issues if issue.code == "legacy_query_expanded"]
    assert len(expanded) == 3
    assert {issue.impact for issue in expanded} == {"behavior_change"}
    assert report.counts.directives_expanded == 3


def test_failed_directives_are_deleted_but_unknown_modifiers_keep_output() -> None:
    report = normalize_query_database(
        [
            _row(
                1,
                "parent",
                "before\n/no-such-row\n/alpha|show 0\n/alpha|mystery\nafter",
            ),
            _row(2, "alpha", "body"),
        ]
    )

    assert report.rows[0].content == "before\n[ 0.alpha ]\nafter"
    assert _codes(report) == [
        "directive_no_match",
        "directive_parse_error",
        "unknown_modifier",
        "interactive_selection_removed",
    ]
    assert [issue.impact for issue in report.issues[-2:]] == [
        "behavior_change",
        "behavior_change",
    ]
    assert report.counts.directives_deleted == 2


def test_redirect_first_row_wins_and_target_must_be_a_final_direct_name() -> None:
    report = normalize_query_database(
        [
            _row(1, "target", "body"),
            _row(2, "gone", "/no-longer-exists"),
        ],
        [
            {"rowid": 6, "alias": "blank", "target": "target"},
            {"rowid": 4, "alias": " good ", "target": " target "},
            {"rowid": 3, "alias": "chain", "target": "alias"},
            {"rowid": 2, "alias": "alias", "target": "target"},
            {"rowid": 1, "alias": "alias", "target": "gone"},
            {"rowid": 0, "alias": "blank", "target": ""},
        ],
    )

    assert [row.rowid for row in report.rows] == [1]
    assert report.redirects == (
        QueryNormalizationRedirect(4, "good", "target"),
    )
    redirect_issues = [
        (issue.rowid, issue.code)
        for issue in report.issues
        if issue.table == "redirect"
    ]
    assert redirect_issues == [
        (0, "missing_redirect_field"),
        (1, "redirect_target_missing"),
        (2, "duplicate_redirect_alias"),
        (3, "redirect_target_missing"),
        (6, "duplicate_redirect_alias"),
    ]
    assert report.counts.redirect_input == 6
    assert report.counts.redirect_output == 1
    assert report.counts.redirect_invalid == 5


def test_match_limit_deletes_only_the_oversized_directive_line() -> None:
    report = normalize_query_database(
        [
            _row(1, "parent", "safe\n/alp"),
            _row(2, "alpha", "a"),
            _row(3, "alpine", "b"),
        ],
        limits=QueryNormalizationLimits(max_matches=1),
    )

    assert report.rows[0].content == "safe"
    assert _codes(report) == ["match_limit_exceeded"]
    assert report.counts.directives_seen == 1
    assert report.counts.directives_deleted == 1
