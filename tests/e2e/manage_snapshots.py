from __future__ import annotations

import difflib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from millify import millify
from rich import print
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from sec_parser import Edgar10QParser
from tests._sec_parser_validation_data import Report, traverse_repository_for_reports
from tests.e2e._overwrite_file import OverwriteResult, overwrite_with_change_track

if TYPE_CHECKING:
    from sec_parser.semantic_elements.abstract_semantic_element import (
        AbstractSemanticElement,
    )

AVAILABLE_ACTIONS = ["update", "verify"]
ALLOWED_MICROSECONDS_PER_CHAR = 1
DEFAULT_YAML_FILTER_PATH = Path(__file__).parent / "e2e_test_data.yaml"


class VerificationFailedError(ValueError):
    pass


@dataclass(frozen=True)
class VerificationResult:
    report: Report
    execution_time_in_seconds: float
    character_count: int
    unexpected_count: int
    missing_count: int
    allowed_execution_time_in_seconds: float

    def errors_found(self) -> bool:
        return (
            self.missing_count > 0
            or self.unexpected_count > 0
            or self.execution_time_in_seconds > self.allowed_execution_time_in_seconds
        )


def compare_elements(
    elements: list[AbstractSemanticElement],
    expected_items: list[dict],
) -> tuple[list, list]:
    unexpected_items, missing_items = [], []
    i, j = 0, 0
    while i < len(elements) and j < len(expected_items):
        el_dict = elements[i].to_dict()
        if {k: v for k, v in el_dict.items() if k != "id"} == {
            k: v for k, v in expected_items[j].items() if k != "id"
        }:
            i += 1
            j += 1
        elif i < j:
            unexpected_items.append(el_dict)
            i += 1
        else:
            missing_items.append(expected_items[j])
            j += 1
    unexpected_items.extend(el.to_dict() for el in elements[i:])
    missing_items.extend(expected_items[j:])
    return unexpected_items, missing_items


def print_verification_result_table(
    results: list[VerificationResult],
) -> None:
    console = Console()
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("Report", style="dim")
    table.add_column(
        "Accuracy",
        justify="center",
    )
    table.add_column("Execution Time\n(Limit, %Limit, Size)", justify="center")

    for result in results:
        if result.missing_count and result.unexpected_count:
            accuracy_str = f"[bold red]{result.missing_count} Missing, {result.unexpected_count} Unexpected[/bold red]"
        elif result.missing_count:
            accuracy_str = f"[bold red]{result.missing_count} Missing[/bold red]"
        elif result.unexpected_count:
            accuracy_str = f"[bold red]{result.unexpected_count} Unexpected[/bold red]"
        else:
            accuracy_str = "[bold green]✓[/bold green]"

        speed_percentage = (
            100
            * result.execution_time_in_seconds
            / result.allowed_execution_time_in_seconds
        )
        speed_str = (
            f"[bold cyan]{result.execution_time_in_seconds * 1000:.0f}ms[/bold cyan] "
            f"([dim]{result.allowed_execution_time_in_seconds * 1000:.0f}ms, "
            f"{speed_percentage:.0f}%, "
            f"{millify(result.character_count)}[/dim])"
        )

        table.add_row(
            f"[bold]{result.report.identifier}[/bold]"
            if result.errors_found()
            else f"{result.report.identifier}",
            accuracy_str,
            speed_str,
        )

    console.print(table)
    print(
        f"[dim]Note: Execution Time Limit is based on a set rate of [bold]{ALLOWED_MICROSECONDS_PER_CHAR}[/bold] microseconds per HTML character (Size).[/dim]\n",
    )


def manage_snapshots(
    action: Literal["update", "verify"],
    data_dir: str,
    document_types: list[str] | None,
    company_names: list[str] | None,
    report_ids: list[str] | None,
    yaml_path_str: str | None,
) -> None:
    if action not in AVAILABLE_ACTIONS:
        msg = f"Invalid action. Available actions are: {AVAILABLE_ACTIONS}"
        raise ValueError(msg)

    yaml_path = Path(yaml_path_str) if yaml_path_str else None
    if (
        not document_types
        and not company_names
        and not report_ids
        and not yaml_path_str
    ):
        if not DEFAULT_YAML_FILTER_PATH.exists():
            msg = f"No filter arguments provided and {yaml_path} does not exist."
            raise FileNotFoundError(msg)
        yaml_path = DEFAULT_YAML_FILTER_PATH

    document_types = list(document_types) if document_types else []
    company_names = list(company_names) if company_names else []
    report_ids = list(report_ids) if report_ids else []
    if yaml_path:
        filters = load_yaml_filter(yaml_path)
        document_types.extend(filters.get("document_types", []))
        company_names.extend(filters.get("company_names", []))
        report_ids.extend(filters.get("report_ids", []))

    if not document_types and not company_names and not report_ids:
        msg = "No filters provided in document_types, company_names, or report_ids."
        raise ValueError(msg)

    results: list[VerificationResult] = []
    generation_results: list[OverwriteResult] = []
    items_not_matching_filters_count = 0
    processed_documents = 0
    for report_detail in traverse_repository_for_reports(Path(data_dir)):
        if (
            (report_detail.document_type not in document_types)
            and (report_detail.company_name not in company_names)
            and (report_detail.report_name not in report_ids)
        ):
            items_not_matching_filters_count += 1
            continue
        processed_documents += 1

        html_file = report_detail.primary_doc_html_path
        expected_json_file = report_detail.expected_elements_json_path
        actual_json_file = report_detail.actual_elements_json_path

        if not html_file.exists():
            msg = f"HTML file not found: {html_file}"
            raise FileNotFoundError(msg)

        with html_file.open("r") as f:
            html_content = f.read()

        execution_time_start = time.perf_counter()
        elements = Edgar10QParser().parse(html_content)
        execution_time_in_seconds = time.perf_counter() - execution_time_start

        if action == "update":
            generation_result = overwrite_with_change_track(
                expected_json_file,
                elements,
            )
            generation_results.append(generation_result)
        else:
            with expected_json_file.open("r") as f:
                expected_contents = f.read()
            dict_items = [e.to_dict() for e in elements]
            actual_contents = json.dumps(
                dict_items,
                indent=4,
            )
            with actual_json_file.open("w") as f:
                f.write(actual_contents)

            missing_count, unexpected_count = show_diff_with_line_numbers(
                expected_contents,
                actual_contents,
                report_detail.identifier,
            )

            character_count = len(html_content)
            allowed_execution_time_in_seconds = (
                character_count * ALLOWED_MICROSECONDS_PER_CHAR / 1_000_000
            )
            result = VerificationResult(
                report=report_detail,
                execution_time_in_seconds=execution_time_in_seconds,
                character_count=character_count,
                allowed_execution_time_in_seconds=allowed_execution_time_in_seconds,
                missing_count=missing_count,
                unexpected_count=unexpected_count,
            )
            results.append(result)

    if not processed_documents:
        msg = "No files found with the given filters."
        raise FileNotFoundError(msg)

    if action == "update":
        removed_lines = 0
        added_lines = 0
        created_files = 0
        modified_files = 0
        unchanged_files = 0
        for result in generation_results:
            if result.created_file:
                created_files += 1
            elif result.removed_lines or result.added_lines:
                modified_files += 1
                removed_lines += result.removed_lines
                added_lines += result.added_lines
            else:
                unchanged_files += 1

        unchanged_files = len(generation_results) - created_files - modified_files
        console = Console()
        summary = "Success! Here's a summary of the changes made:\n"
        if created_files != 0:
            summary += f"- [bold green]New files:[/bold green] {created_files}\n"
        if modified_files != 0:
            summary += (
                f"- [bold yellow]Modified files:[/bold yellow] {modified_files}\n"
            )
        if unchanged_files != 0:
            summary += f"- [bold blue]Unchanged files:[/bold blue] {unchanged_files}\n"
        if removed_lines != 0:
            summary += f"  - [bold red]Removed lines:[/bold red] {removed_lines}\n"
        if added_lines != 0:
            summary += f"  - [bold green]Added lines:[/bold green] {added_lines}\n"
        if items_not_matching_filters_count != 0:
            summary += f"- [bold]Filtered out (skipped):[/bold] {items_not_matching_filters_count}\n"
        console.print(Panel(summary.strip()))
    elif action == "verify":
        print_verification_result_table(results)
        if any(result.errors_found() for result in results):
            msg = "[ERROR] Verification failed."
            raise VerificationFailedError(msg)
        print("Verification of the end-to-end snapshots completed successfully.")


def show_diff_with_line_numbers(expected, actual, identifier):
    identifier = identifier.ljust(25)
    word1, word2 = "\[expected]:", "\[actual]:"
    d = difflib.Differ()
    diff = list(d.compare(expected.splitlines(), actual.splitlines()))

    line_number_expected = 0
    line_number_actual = 0
    missing_count = 0
    unexpected_count = 0
    for line in diff:
        if line.startswith("  "):
            line_number_expected += 1
            line_number_actual += 1
        elif line.startswith("- "):
            print(
                f'"{identifier}" Line {line_number_expected + 1} {word1.ljust(len(word2))} {line[2:].strip()}',
            )
            missing_count += 1
            line_number_expected += 1
        elif line.startswith("+ "):
            print(
                f'"{identifier}" Line {line_number_actual + 1} {word2.ljust(len(word1))} {line[2:].strip()}',
            )
            unexpected_count += 1
            line_number_actual += 1
    return missing_count, unexpected_count


def load_yaml_filter(file_path: Path) -> dict:
    with file_path.open("r") as stream:
        try:
            return yaml.safe_load(stream)
        except yaml.YAMLError as e:
            print(f"Error reading YAML file: {e}")
            return {}
