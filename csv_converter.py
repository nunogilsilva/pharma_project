"""Convert a legacy Excel report to a compact CSV and print its DataFrame."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Union

import pandas as pd


DEFAULT_INPUT = Path(__file__).parent / "files" / "labs_stock" / "cooprofar.xls"
HEADER_HINTS = ("produto", "designação", "stk. tot", "stk. loc", "v. vendas")


def clean_text(value: object) -> object:
    """Collapse formatting whitespace while preserving missing values."""
    if isinstance(value, str):
        return " ".join(value.split())
    return value


def prepare_workbook(
    input_path: Path,
    sheet_name: Union[str, int] = 0,
    header_row: int | None = None,
    report_format: str = "stock",
) -> pd.DataFrame:
    """Read a report, remove empty spacing, and use its actual header row."""
    if not input_path.is_file():
        raise FileNotFoundError(f"Excel file not found: {input_path}")

    raw = pd.read_excel(
        input_path,
        sheet_name=sheet_name,
        header=None,
        engine="xlrd",
    )
    raw = raw.map(clean_text)
    raw = raw.dropna(axis=0, how="all").reset_index(drop=True)

    if raw.empty:
        raise ValueError("The worksheet does not contain any data.")

    if header_row is None:
        header_row = find_header_row(raw)
    if header_row < 0 or header_row >= len(raw):
        raise ValueError(f"Header row must be between 0 and {len(raw) - 1}.")

    headers = [
        str(value).strip()
        for value in raw.iloc[header_row].tolist()
        if pd.notna(value)
    ]
    compact_rows = raw.apply(
        lambda row: [value for value in row.tolist() if pd.notna(value)],
        axis=1,
    )
    row_values = compact_rows.iloc[header_row + 1 :]
    compact = compact_report_rows if report_format == "stock" else compact_generic_rows
    records = compact(row_values, len(headers))
    dataframe = pd.DataFrame(records, columns=headers)
    dataframe.columns = make_unique(headers)
    return dataframe.dropna(axis=1, how="all").reset_index(drop=True)


def compact_report_rows(rows: pd.Series, column_count: int) -> list[list[object]]:
    """Combine wrapped product descriptions with their sales data row."""
    records: list[list[object]] = []
    current: list[object] | None = None

    for values in rows:
        starts_product = (
            len(values) >= 2
            and pd.notna(values[0])
            and isinstance(values[1], str)
        )
        has_data = len(values) > 2

        if starts_product:
            if current is not None:
                records.append(current)
            current = values[:2] + [None] * (column_count - 2)
        elif current is not None and has_data:
            current[2:] = values[: column_count - 2]
        elif current is not None and values:
            description = str(current[1])
            continuation = str(values[0])
            current[1] = f"{description} {continuation}".strip()

    if current is not None:
        records.append(current)
    return records


def compact_generic_rows(rows: pd.Series, column_count: int) -> list[list[object]]:
    """Compact the molecule report, which may place data on the product row."""
    records: list[list[object]] = []
    current: list[object] | None = None

    for values in rows:
        if any(str(value).casefold() == "total de referências" for value in values):
            break

        starts_product = (
            len(values) >= 2
            and pd.notna(pd.to_numeric(values[0], errors="coerce"))
            and isinstance(values[1], str)
        )
        has_data = (
            current is not None
            and len(values) > 2
            and pd.notna(pd.to_numeric(values[0], errors="coerce"))
        )

        if starts_product:
            if current is not None:
                records.append(current)
            current = values[:2] + [None] * (column_count - 2)
            current[2:] = values[2:column_count]
        elif current is not None and has_data:
            current[2:] = values[: column_count - 2]
        elif current is not None and len(values) == 1:
            current[1] = f"{current[1]} {values[0]}".strip()

    if current is not None:
        records.append(current)
    return records


def find_header_row(dataframe: pd.DataFrame) -> int:
    """Find the report header by matching recognizable column labels."""
    for index, row in dataframe.iterrows():
        values = {str(value).casefold() for value in row.dropna()}
        matches = sum(
            any(hint in value for value in values) for hint in HEADER_HINTS
        )
        if matches >= 2:
            return int(index)
    raise ValueError(
        "Could not find the header row automatically. "
        "Use --header-row with the compacted row number."
    )


def make_unique(columns: list[str]) -> list[str]:
    """Make blank or repeated column names safe for CSV/DataFrame use."""
    counts: dict[str, int] = {}
    result = []
    for column in columns:
        name = column or "column"
        counts[name] = counts.get(name, 0) + 1
        result.append(name if counts[name] == 1 else f"{name}_{counts[name]}")
    return result


def convert_to_csv(
    input_path: Path,
    output_path: Path,
    sheet_name: Union[str, int] = 0,
    header_row: int | None = None,
    report_format: str = "stock",
) -> Path:
    """Convert one worksheet from a legacy .xls workbook to CSV."""
    dataframe = prepare_workbook(
        input_path,
        sheet_name,
        header_row,
        report_format,
    )
    dataframe.to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def read_csv(path: Path) -> pd.DataFrame:
    """Read a converted CSV file."""
    if not path.is_file():
        raise FileNotFoundError(f"CSV file not found: {path}")

    return pd.read_csv(path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert an .xls file to CSV and print it as a pandas DataFrame."
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Path to the .xls file (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output CSV path (default: next to the input file with a .csv suffix).",
    )
    parser.add_argument(
        "--sheet",
        default=0,
        help="Worksheet name or zero-based index (default: first worksheet).",
    )
    parser.add_argument(
        "--header-row",
        type=int,
        help="Compacted zero-based header row; automatic detection is the default.",
    )
    parser.add_argument(
        "--format",
        choices=("stock", "generic"),
        default="stock",
        help="Report structure to use (default: stock).",
    )
    args = parser.parse_args()
    output_path = args.output or args.input_file.with_suffix(".csv")

    try:
        sheet_name: Union[str, int] = (
            int(args.sheet) if str(args.sheet).isdigit() else args.sheet
        )
        csv_path = convert_to_csv(
            args.            input_file,
            output_path,
            sheet_name,
            args.header_row,
            args.format,
        )
        dataframe = read_csv(csv_path)
    except (FileNotFoundError, ValueError, ImportError) as error:
        parser.error(str(error))

    print(f"Converted workbook to: {csv_path}\n")
    with pd.option_context(
        "display.max_rows", None,
        "display.max_columns", None,
        "display.width", None,
    ):
        print(dataframe.to_string(index=False))


if __name__ == "__main__":
    main()