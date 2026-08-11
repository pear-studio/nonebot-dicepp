"""Pure normalization for legacy DicePP query database rows.

The caller owns all database I/O.  This module accepts rows that have already
been read, applies the frozen legacy-v1 embedded-query behavior, and returns
immutable values suitable for inspection or writing to a replacement file.
Only the Python standard library is used so every DicePP process can share the
same contract.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal


MAX_LEGACY_QUERY_MATCHES = 1000
MAX_LEGACY_DIRECTIVES = 1000
MAX_LEGACY_DIRECTIVE_CHARS = 4096
MAX_NORMALIZED_CONTENT_CHARS = 1_000_000


@dataclass(frozen=True, slots=True)
class QueryNormalizationLimits:
    """Resource limits for one normalization run."""

    max_matches: int = MAX_LEGACY_QUERY_MATCHES
    max_directives: int = MAX_LEGACY_DIRECTIVES
    max_directive_chars: int = MAX_LEGACY_DIRECTIVE_CHARS
    max_output_chars: int = MAX_NORMALIZED_CONTENT_CHARS

    def __post_init__(self) -> None:
        values = (
            self.max_matches,
            self.max_directives,
            self.max_directive_chars,
            self.max_output_chars,
        )
        if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in values):
            raise ValueError("normalization limits must be positive integers")
        if self.max_matches > MAX_LEGACY_QUERY_MATCHES:
            raise ValueError(
                f"max_matches cannot exceed {MAX_LEGACY_QUERY_MATCHES}"
            )


DEFAULT_QUERY_NORMALIZATION_LIMITS = QueryNormalizationLimits()


@dataclass(frozen=True, slots=True)
class QueryNormalizationRow:
    """One final data row using DicePP's four logical fields."""

    rowid: int
    name: str
    english: str
    source: str
    content: str


@dataclass(frozen=True, slots=True)
class QueryNormalizationRedirect:
    """One validated direct alias-to-name redirect."""

    rowid: int
    alias: str
    target: str


@dataclass(frozen=True, slots=True)
class QueryNormalizationIssue:
    """A stable, machine-readable normalization finding."""

    code: str
    table: Literal["data", "redirect"]
    rowid: int
    line_number: int | None
    message: str
    subject: str = ""
    impact: Literal["deletion", "behavior_change"] = "deletion"
    related_rowids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class QueryNormalizationCounts:
    """Structured input, output, and decision counts."""

    data_input: int
    data_output: int
    data_invalid: int
    data_duplicates: int
    directives_seen: int
    directives_expanded: int
    directives_deleted: int
    redirect_input: int
    redirect_output: int
    redirect_invalid: int
    issue_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class QueryNormalizationReport:
    """Complete deterministic result of a normalization run."""

    rows: tuple[QueryNormalizationRow, ...]
    redirects: tuple[QueryNormalizationRedirect, ...]
    counts: QueryNormalizationCounts
    issues: tuple[QueryNormalizationIssue, ...]

    @property
    def data_rows(self) -> tuple[QueryNormalizationRow, ...]:
        """Explicit alias for callers that name the output table."""
        return self.rows

    @property
    def redirect_rows(self) -> tuple[QueryNormalizationRedirect, ...]:
        """Explicit alias for callers that name the output table."""
        return self.redirects


@dataclass(frozen=True, slots=True)
class _LegacyRow:
    rowid: int
    name: str
    english: str
    source: str
    category: str
    tag: str
    content: str


@dataclass(frozen=True, slots=True)
class _LegacyRedirect:
    rowid: int
    alias: str
    target: str


class _LegacyParseError(ValueError):
    pass


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _mapping_value(row: object, names: tuple[str, ...], default: object = "") -> object:
    if isinstance(row, Mapping):
        for name in names:
            if name in row:
                return row[name]
        return default
    keys = getattr(row, "keys", None)
    if callable(keys):
        available = set(keys())
        for name in names:
            if name in available:
                return row[name]  # type: ignore[index]
    return default


def _rowid(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("rowid must be an integer")
    try:
        result = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("rowid must be an integer") from exc
    return result


def _read_data_row(row: object) -> _LegacyRow:
    if isinstance(row, QueryNormalizationRow):
        return _LegacyRow(
            row.rowid, row.name, row.english, row.source, "", "", row.content
        )
    if isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)):
        if len(row) == 7:
            rowid, name, english, source, category, tag, content = row
        elif len(row) == 5:
            rowid, name, english, source, content = row
            category = tag = ""
        else:
            raise ValueError("data row sequences must contain 5 or 7 values")
    else:
        rowid = _mapping_value(row, ("rowid", "_rowid"), None)
        name = _mapping_value(row, ("name", "名称"))
        english = _mapping_value(row, ("english", "name_en", "英文"))
        source = _mapping_value(row, ("source", "来源"))
        category = _mapping_value(row, ("category", "catalogue", "分类"))
        tag = _mapping_value(row, ("tag", "标签"))
        content = _mapping_value(row, ("content", "内容"))
    return _LegacyRow(
        _rowid(rowid),
        _text(name).strip(),
        _text(english),
        _text(source).strip(),
        _text(category),
        _text(tag),
        _text(content),
    )


def _read_redirect_row(row: object) -> _LegacyRedirect:
    if isinstance(row, QueryNormalizationRedirect):
        return _LegacyRedirect(row.rowid, row.alias, row.target)
    if isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)):
        if len(row) != 3:
            raise ValueError("redirect row sequences must contain 3 values")
        rowid, alias, target = row
    else:
        rowid = _mapping_value(row, ("rowid", "_rowid"), None)
        alias = _mapping_value(row, ("alias", "name", "名称"))
        target = _mapping_value(row, ("target", "redirect", "重定向"))
    return _LegacyRedirect(
        _rowid(rowid), _text(alias).strip(), _text(target).strip()
    )


def legacy_v1_command_split(keywords: str) -> tuple[str, ...]:
    """Frozen copy of the historical ``command_split`` tokenizer."""
    result: list[str] = []
    collected = ""
    prefix = ""
    quoted = False
    for character in keywords:
        if not quoted and collected == "" and character == '"':
            quoted = True
        elif quoted and character == '"':
            quoted = False
            if collected != "":
                result.append(prefix + collected)
                prefix = ""
                collected = ""
        elif not quoted and character in "#&":
            if collected.strip():
                result.append(prefix + collected.strip())
            collected = ""
            prefix = character
        elif not quoted and character == " ":
            if collected.strip():
                result.append(prefix + collected.strip())
            collected = ""
            prefix = ""
        else:
            collected += character
    if quoted:
        result.append(prefix + collected)
    elif collected.strip():
        result.append(prefix + collected.strip())
    return tuple(result)


def _legacy_regexp_escape(value: str) -> str:
    result = ""
    for character in value:
        if character in "$()*+.[?\\^{|":
            result += "\\" + character
        else:
            result += character
    return result


def _regexp_group_matches(value: str, commands: tuple[str, ...]) -> bool:
    expressions: list[str] = []
    for command in commands:
        if command.startswith("-") and len(command) > 1:
            expressions.append(f"^(?!.*{_legacy_regexp_escape(command[1:])})")
        elif command.startswith("=") and len(command) > 1:
            expressions.append(f"^{_legacy_regexp_escape(command[1:])}$")
        elif command:
            expressions.append(_legacy_regexp_escape(command))
    if not expressions:
        return False
    try:
        return re.search("|".join(expressions), value, re.IGNORECASE) is not None
    except re.error as exc:
        raise _LegacyParseError(str(exc)) from exc


def _category_group_matches(value: str, commands: tuple[str, ...]) -> bool:
    included: list[str] = []
    excluded: list[str] = []
    for command in commands:
        if command.startswith("-") and len(command) > 1:
            excluded.append(command[1:])
        elif command.startswith("=") and len(command) > 1:
            included.append(command[1:])
        elif command:
            included.append(command)
    included_match = bool(included) and value in included
    excluded_match = bool(excluded) and value not in excluded
    return included_match or excluded_match


@dataclass(frozen=True, slots=True)
class _Query:
    name_groups: tuple[tuple[str, ...], ...]
    hash_groups: tuple[tuple[str, ...], ...]
    category_groups: tuple[tuple[str, ...], ...]
    complete_name: str
    complete_english: str
    can_single_query: bool


def _parse_query(value: str) -> _Query:
    name_groups: list[tuple[str, ...]] = []
    hash_groups: list[tuple[str, ...]] = []
    category_groups: list[tuple[str, ...]] = []
    complete_name = ""
    complete_english = ""
    can_single_query = True
    for token in legacy_v1_command_split(value):
        if not token:
            continue
        target = name_groups
        command = token
        if token[0] == "#":
            target = hash_groups
            command = token[1:]
            can_single_query = False
        elif token[0] == "&":
            target = category_groups
            command = token[1:]
            can_single_query = False
        choices = tuple(command.split("/"))
        if not any(choices):
            continue
        target.append(choices)
        if target is name_groups:
            if len(choices) == 1:
                complete_name += choices[0]
                complete_english = (
                    f"{complete_english} {choices[0]}" if complete_english else choices[0]
                )
            else:
                complete_name = ""
                complete_english = ""
    return _Query(
        tuple(name_groups),
        tuple(hash_groups),
        tuple(category_groups),
        complete_name,
        complete_english,
        can_single_query,
    )


def _matches_non_name(row: _LegacyRow, query: _Query) -> bool:
    hash_text = row.source + row.category + row.tag
    return all(_regexp_group_matches(hash_text, group) for group in query.hash_groups) and all(
        _category_group_matches(row.category, group)
        for group in query.category_groups
    )


def _matches_row(row: _LegacyRow, query: _Query) -> bool:
    name_text = row.name + row.english
    return _matches_non_name(row, query) and all(
        _regexp_group_matches(name_text, group) for group in query.name_groups
    )


def _resolve_query(
    value: str,
    rows: tuple[_LegacyRow, ...],
    redirects: tuple[_LegacyRedirect, ...],
    max_matches: int,
) -> tuple[_LegacyRow, ...]:
    query = _parse_query(value)
    condition_count = len(query.name_groups) + len(query.hash_groups) + len(query.category_groups)
    if condition_count == 0:
        return ()

    candidates = [row for row in rows if _matches_row(row, query)]
    if query.name_groups:
        for redirect in redirects:
            if not all(
                _regexp_group_matches(redirect.alias, group)
                for group in query.name_groups
            ):
                continue
            candidates.extend(
                row
                for row in rows
                if _matches_non_name(row, query)
                and row.name == redirect.target
            )

    unique: list[_LegacyRow] = []
    seen: set[tuple[str, str]] = set()
    for row in candidates:
        identity = (row.name, row.source)
        if identity not in seen:
            seen.add(identity)
            unique.append(row)

    if query.can_single_query:
        exact = [
            row
            for row in unique
            if (query.complete_name and row.name == query.complete_name)
            or (
                query.complete_english
                and row.english.casefold() == query.complete_english.casefold()
            )
        ]
        if exact:
            unique = exact
    if len(unique) > max_matches:
        raise OverflowError("legacy query matched too many rows")
    return tuple(unique)


def _show_output(
    matches: tuple[_LegacyRow, ...], start_index: int, limit: int, max_chars: int
) -> str:
    rendered: list[str] = []
    size = 0
    for index, row in enumerate(matches, start=start_index):
        clipped = row.content[:limit]
        flattened = " ".join(clipped.splitlines())
        suffix = "..." if len(row.content) > limit else ""
        line = f"{index}.{row.name} : {flattened}{suffix}"
        size += len(line) + (1 if rendered else 0)
        if size > max_chars:
            raise OverflowError("legacy directive output is too large")
        rendered.append(line)
    return "\n".join(rendered)


def normalize_query_database(
    data_rows: Iterable[object],
    redirect_rows: Iterable[object] = (),
    *,
    limits: QueryNormalizationLimits = DEFAULT_QUERY_NORMALIZATION_LIMITS,
) -> QueryNormalizationReport:
    """Normalize already-read legacy rows without opening a database.

    Mapping inputs may use either logical English keys or the original Chinese
    column names.  Sequence data rows use
    ``(rowid, name, english, source, category, tag, content)`` (or the final
    five-field shape without category/tag); redirect rows use
    ``(rowid, alias, target)``.
    """
    if not isinstance(limits, QueryNormalizationLimits):
        raise TypeError("limits must be QueryNormalizationLimits")

    raw_rows = tuple(
        row
        for _, row in sorted(
            enumerate(_read_data_row(row) for row in data_rows),
            key=lambda item: (item[1].rowid, item[0]),
        )
    )
    raw_redirects = tuple(
        row
        for _, row in sorted(
            enumerate(_read_redirect_row(row) for row in redirect_rows),
            key=lambda item: (item[1].rowid, item[0]),
        )
    )
    issues: list[QueryNormalizationIssue] = []
    data_subjects = {row.rowid: row.name for row in raw_rows}
    redirect_subjects = {row.rowid: row.alias for row in raw_redirects}

    def add_issue(
        code: str,
        table: Literal["data", "redirect"],
        rowid: int,
        message: str,
        *,
        line_number: int | None = None,
        impact: Literal["deletion", "behavior_change"] = "deletion",
        related_rowids: tuple[int, ...] = (),
    ) -> None:
        issues.append(
            QueryNormalizationIssue(
                code=code,
                table=table,
                rowid=rowid,
                line_number=line_number,
                message=message,
                subject=(
                    data_subjects.get(rowid, "")
                    if table == "data"
                    else redirect_subjects.get(rowid, "")
                ),
                impact=impact,
                related_rowids=related_rowids,
            )
        )

    accepted: list[_LegacyRow] = []
    identities: dict[tuple[str, str], _LegacyRow] = {}
    invalid_data = 0
    duplicate_data = 0
    for row in raw_rows:
        missing = tuple(
            field
            for field, value in (("name", row.name), ("content", row.content.strip()))
            if not value
        )
        if missing:
            invalid_data += 1
            add_issue(
                "missing_required_field",
                "data",
                row.rowid,
                "缺少必填字段（" + "、".join(missing) + "），修复时会删除该词条。",
            )
            continue
        if len(row.content) > limits.max_output_chars:
            invalid_data += 1
            add_issue(
                "content_limit_exceeded",
                "data",
                row.rowid,
                f"内容超过 {limits.max_output_chars} 字符，修复时会删除该词条。",
            )
            continue
        identity = (row.name, row.source)
        first = identities.get(identity)
        if first is not None:
            duplicate_data += 1
            conflict = first.content != row.content
            add_issue(
                "duplicate_content_conflict" if conflict else "duplicate_identity",
                "data",
                row.rowid,
                f"名称和来源与数据库第 {first.rowid} 行相同；修复时保留第 {first.rowid} 行，删除当前行"
                + ("。两行内容不同，请确认这样处理是否符合预期。" if conflict else "。"),
                related_rowids=(first.rowid,),
            )
            continue
        identities[identity] = row
        accepted.append(row)

    # The resolver may use only first-row aliases whose targets directly exist
    # in the valid, deduplicated snapshot.  Final redirect validation is repeated
    # after directive processing because a target row can subsequently vanish.
    initial_names = {row.name for row in accepted}
    resolver_redirects: list[_LegacyRedirect] = []
    resolver_aliases: set[str] = set()
    for redirect in raw_redirects:
        if not redirect.alias or redirect.alias in resolver_aliases:
            if redirect.alias:
                resolver_aliases.add(redirect.alias)
            continue
        resolver_aliases.add(redirect.alias)
        if redirect.target and redirect.target in initial_names:
            resolver_redirects.append(redirect)

    directives_seen = 0
    directives_expanded = 0
    directives_deleted = 0
    final_rows: list[QueryNormalizationRow] = []
    accepted_tuple = tuple(accepted)
    resolver_redirect_tuple = tuple(resolver_redirects)
    for row in accepted:
        output_lines: list[str] = []
        prior_match_count = 0
        for line_number, line in enumerate(row.content.splitlines(), start=1):
            if not line.startswith("/"):
                output_lines.append(line)
                continue
            directives_seen += 1
            if directives_seen > limits.max_directives:
                directives_deleted += 1
                add_issue(
                    "directive_limit_exceeded",
                    "data",
                    row.rowid,
                    f"数据库中的过时查询超过 {limits.max_directives} 条，本行无法处理，修复时会删除这一行内容。",
                    line_number=line_number,
                )
                continue
            if len(line) - 1 > limits.max_directive_chars:
                directives_deleted += 1
                add_issue(
                    "directive_length_exceeded",
                    "data",
                    row.rowid,
                    f"这条过时查询超过 {limits.max_directive_chars} 字符，修复时会删除这一行内容。",
                    line_number=line_number,
                )
                continue

            command_parts = line[1:].lower().split("|")
            query_text = command_parts[0]
            try:
                matches = _resolve_query(
                    query_text,
                    accepted_tuple,
                    resolver_redirect_tuple,
                    limits.max_matches,
                )
            except OverflowError:
                directives_deleted += 1
                add_issue(
                    "match_limit_exceeded",
                    "data",
                    row.rowid,
                    f"这条过时查询匹配超过 {limits.max_matches} 个词条，无法安全展开，修复时会删除这一行内容。",
                    line_number=line_number,
                )
                continue
            except _LegacyParseError as exc:
                directives_deleted += 1
                add_issue(
                    "directive_parse_error",
                    "data",
                    row.rowid,
                    f"无法解析这条过时查询（{exc}），修复时会删除这一行内容。",
                    line_number=line_number,
                )
                continue
            if not matches:
                directives_deleted += 1
                add_issue(
                    "directive_no_match",
                    "data",
                    row.rowid,
                    "这条过时查询已经匹配不到任何词条，修复时会删除这一行内容。",
                    line_number=line_number,
                )
                continue

            replacement = "[ " + ", ".join(
                f"{prior_match_count + index}.{match.name}"
                for index, match in enumerate(matches)
            ) + " ]"
            used_show = False
            parse_failed = False
            for raw_modifier in command_parts[1:]:
                modifier = raw_modifier.strip()
                if modifier.startswith("clear") and len(modifier) > 5:
                    replacement = replacement.replace(modifier[5:].strip(), "")
                elif modifier.startswith("show"):
                    raw_limit = modifier[4:].strip()
                    try:
                        show_limit = 200 if not raw_limit else int(raw_limit)
                        if show_limit <= 0:
                            raise ValueError
                    except ValueError:
                        directives_deleted += 1
                        add_issue(
                            "directive_parse_error",
                            "data",
                            row.rowid,
                            "show 后必须填写正整数，当前写法无法处理；修复时会删除这一行内容。",
                            line_number=line_number,
                        )
                        parse_failed = True
                        break
                    try:
                        replacement = _show_output(
                            matches,
                            prior_match_count,
                            show_limit,
                            limits.max_output_chars,
                        )
                    except OverflowError:
                        directives_deleted += 1
                        add_issue(
                            "directive_output_limit_exceeded",
                            "data",
                            row.rowid,
                            f"展开结果超过 {limits.max_output_chars} 字符，修复时会删除这一行内容。",
                            line_number=line_number,
                        )
                        parse_failed = True
                        break
                    used_show = True
                else:
                    add_issue(
                        "unknown_modifier",
                        "data",
                        row.rowid,
                        f"无法识别过时查询参数“{modifier or '<空>'}”，修复时会忽略这个参数。",
                        line_number=line_number,
                        impact="behavior_change",
                    )
            if parse_failed:
                continue
            if len(replacement) > limits.max_output_chars:
                directives_deleted += 1
                add_issue(
                    "directive_output_limit_exceeded",
                    "data",
                    row.rowid,
                    f"展开结果超过 {limits.max_output_chars} 字符，修复时会删除这一行内容。",
                    line_number=line_number,
                )
                continue
            output_lines.append(replacement)
            directives_expanded += 1
            if used_show:
                add_issue(
                    "legacy_query_expanded",
                    "data",
                    row.rowid,
                    "这条过时查询会在修复时展开为静态内容，以后不会再随被引用词条变化。",
                    line_number=line_number,
                    impact="behavior_change",
                    related_rowids=tuple(match.rowid for match in matches),
                )
            else:
                add_issue(
                    "interactive_selection_removed",
                    "data",
                    row.rowid,
                    "这条过时查询会转换成静态结果列表，转换后不能再通过序号继续选择。",
                    line_number=line_number,
                    impact="behavior_change",
                    related_rowids=tuple(match.rowid for match in matches),
                )
            prior_match_count += len(matches)

        content = "\n".join(output_lines)
        if not content.strip():
            invalid_data += 1
            add_issue(
                "empty_content_after_directives",
                "data",
                row.rowid,
                "处理过时查询后词条内容为空，修复时会删除整个词条。",
            )
            continue
        if len(content) > limits.max_output_chars:
            invalid_data += 1
            add_issue(
                "content_limit_exceeded",
                "data",
                row.rowid,
                f"处理后的内容超过 {limits.max_output_chars} 字符，修复时会删除整个词条。",
            )
            continue
        final_rows.append(
            QueryNormalizationRow(
                row.rowid, row.name, row.english, row.source, content
            )
        )

    final_names = {row.name for row in final_rows}
    final_redirects: list[QueryNormalizationRedirect] = []
    seen_aliases: dict[str, int] = {}
    redirect_invalid = 0
    for redirect in raw_redirects:
        if not redirect.alias:
            redirect_invalid += 1
            add_issue(
                "missing_redirect_field",
                "redirect",
                redirect.rowid,
                "重定向缺少名称或目标，修复时会删除这条重定向。",
            )
            continue
        first_rowid = seen_aliases.get(redirect.alias)
        if first_rowid is not None:
            redirect_invalid += 1
            add_issue(
                "duplicate_redirect_alias",
                "redirect",
                redirect.rowid,
                f"重定向名称与数据库第 {first_rowid} 行相同；修复时保留第 {first_rowid} 行，删除当前行。",
                related_rowids=(first_rowid,),
            )
            continue
        seen_aliases[redirect.alias] = redirect.rowid
        if not redirect.target:
            redirect_invalid += 1
            add_issue(
                "missing_redirect_field",
                "redirect",
                redirect.rowid,
                "重定向缺少名称或目标，修复时会删除这条重定向。",
            )
            continue
        if redirect.target not in final_names:
            redirect_invalid += 1
            add_issue(
                "redirect_target_missing",
                "redirect",
                redirect.rowid,
                "重定向目标不是修复后保留的词条名称，修复时会删除这条重定向。",
            )
            continue
        final_redirects.append(
            QueryNormalizationRedirect(
                redirect.rowid, redirect.alias, redirect.target
            )
        )

    issue_counts = tuple(sorted(Counter(issue.code for issue in issues).items()))
    counts = QueryNormalizationCounts(
        data_input=len(raw_rows),
        data_output=len(final_rows),
        data_invalid=invalid_data,
        data_duplicates=duplicate_data,
        directives_seen=directives_seen,
        directives_expanded=directives_expanded,
        directives_deleted=directives_deleted,
        redirect_input=len(raw_redirects),
        redirect_output=len(final_redirects),
        redirect_invalid=redirect_invalid,
        issue_counts=issue_counts,
    )
    return QueryNormalizationReport(
        tuple(final_rows), tuple(final_redirects), counts, tuple(issues)
    )


__all__ = [
    "DEFAULT_QUERY_NORMALIZATION_LIMITS",
    "MAX_LEGACY_DIRECTIVE_CHARS",
    "MAX_LEGACY_DIRECTIVES",
    "MAX_LEGACY_QUERY_MATCHES",
    "MAX_NORMALIZED_CONTENT_CHARS",
    "QueryNormalizationCounts",
    "QueryNormalizationIssue",
    "QueryNormalizationLimits",
    "QueryNormalizationRedirect",
    "QueryNormalizationReport",
    "QueryNormalizationRow",
    "legacy_v1_command_split",
    "normalize_query_database",
]
