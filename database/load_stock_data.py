"""Load lab stock CSV exports into the PostgreSQL star schema."""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT_DIRECTORY = ROOT / "files" / "labs_stock"
DEFAULT_DATABASE_URL = "postgresql://pharma:pharma@localhost:5432/pharma"
MONTHS = {
    "JAN": 1,
    "FEV": 2,
    "MAR": 3,
    "ABR": 4,
    "MAI": 5,
    "JUN": 6,
    "JUL": 7,
    "AGO": 8,
    "SET": 9,
    "OUT": 10,
    "NOV": 11,
    "DEZ": 12,
}
MONTH_PATTERN = re.compile(r"^(" + "|".join(MONTHS) + r")(?:_\d+)?$", re.I)


def parse_month_end(value: str) -> date:
    try:
        year_text, month_text = value.split("-", maxsplit=1)
        return date(int(year_text), int(month_text), 1)
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError("Use YYYY-MM for the sales period end.") from error


def parse_file_month(value: str) -> tuple[str, date]:
    try:
        filename, month = value.split("=", maxsplit=1)
        if not filename.strip():
            raise ValueError
        return filename.strip(), parse_month_end(month.strip())
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Use FILENAME.csv=YYYY-MM for a per-file sales period."
        ) from error


def month_columns(headers: list[str]) -> list[tuple[str, int]]:
    found = []
    for header in headers:
        match = MONTH_PATTERN.fullmatch(header.strip())
        if match:
            found.append((header, MONTHS[match.group(1).upper()]))
    return found


def assign_months(
    columns: list[tuple[str, int]], period_end: date
) -> list[tuple[str, date]]:
    if not columns:
        raise ValueError("No Portuguese monthly sales columns were found.")
    if columns[-1][1] != period_end.month:
        raise ValueError(
            f"The rightmost sales column is month {columns[-1][1]}, but "
            f"--sales-period-end is {period_end:%Y-%m}."
        )

    periods_reversed: list[tuple[str, date]] = []
    year, month = period_end.year, period_end.month
    for header, header_month in reversed(columns):
        if header_month != month:
            raise ValueError(f"Monthly sales columns are not consecutive at {header!r}.")
        periods_reversed.append((header, date(year, month, 1)))
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    return list(reversed(periods_reversed))


def parse_number(
    value: str | None, *, blank: Decimal | None = None
) -> Decimal | None:
    if value is None or not value.strip():
        return blank
    cleaned = re.sub(r"[^\d,.\-]", "", value.strip())
    if not cleaned:
        return blank
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except ValueError as error:
        raise ValueError(f"Invalid numeric value: {value!r}") from error


def required_column(headers: list[str], name: str) -> str:
    aliases = {
        "product_code": ("Código", "Codigo", "Código do produto"),
        "product_name": ("Designação", "Designacao", "Produto"),
        "stock_total": ("Stk. Tot", "Stock Total"),
        "stock_local": ("Stk. Loc", "Stock Local"),
        "retail_price": ("P.V.P.", "Preço Venda", "Preco Venda"),
        "sales_value": ("V. Vendas", "Vendas"),
        "unit_cost": ("P.Custo", "Preço Custo", "Preco Custo"),
        "total_units_sold": ("TUnd", "Total Unidades"),
        "margin": ("MG %", "Margem", "Margem Bruta"),
        "location": ("Local",),
    }
    normalized = {header.strip().casefold(): header for header in headers}
    for alias in aliases[name]:
        if alias.casefold() in normalized:
            return normalized[alias.casefold()]
    raise ValueError(f"Could not find a CSV column for {name!r}.")


def read_stock_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as csv_file:
        rows = csv.reader(csv_file)
        headers = next(
            (row for row in rows if any(cell.strip() for cell in row)), None
        )
        if headers is None:
            raise ValueError(f"{path} has no CSV header.")
        records = list(csv.DictReader(csv_file, fieldnames=headers))
    return headers, records


def get_or_create_dimension(
    cursor: Any,
    table: str,
    key_column: str,
    value: str,
    value_column: str | None = None,
) -> int:
    if value_column:
        cursor.execute(
            f"INSERT INTO {table} ({key_column}, {value_column}) VALUES (%s, %s) "
            f"ON CONFLICT ({key_column}) DO UPDATE SET {value_column} = EXCLUDED.{value_column} "
            f"{', updated_at = CURRENT_TIMESTAMP' if table == 'dim_product' else ''} "
            f"RETURNING {table.removeprefix('dim_')}_id",
            (value, value),
        )
    else:
        cursor.execute(
            f"INSERT INTO {table} ({key_column}) VALUES (%s) "
            f"ON CONFLICT ({key_column}) DO UPDATE SET {key_column} = EXCLUDED.{key_column} "
            f"RETURNING {table.removeprefix('dim_')}_id",
            (value,),
        )
    return cursor.fetchone()[0]


def load_file(
    path: Path, snapshot_date: date, period_end: date, database_url: str
) -> int:
    headers, records = read_stock_csv(path)
    columns = {
        field: required_column(headers, field)
        for field in (
            "product_code",
            "product_name",
            "stock_total",
            "stock_local",
            "retail_price",
            "sales_value",
            "unit_cost",
            "total_units_sold",
            "margin",
            "location",
        )
    }
    periods = assign_months(month_columns(headers), period_end)

    file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    supplier_code = path.stem.casefold()
    supplier_name = path.stem
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO etl_batch
                (source_file, file_sha256, snapshot_date, sales_period_end)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source_file, file_sha256, snapshot_date, sales_period_end)
            DO UPDATE SET source_file = EXCLUDED.source_file
            RETURNING batch_id
            """,
            (path.name, file_hash, snapshot_date, period_end),
        )
        batch_id = cursor.fetchone()[0]
        supplier_id = get_or_create_dimension(
            cursor, "dim_supplier", "supplier_code", supplier_code, "supplier_name"
        )
        for _, month_start in periods:
            cursor.execute(
                """
                INSERT INTO dim_month
                    (month_start, calendar_year, month_number, quarter)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (month_start) DO NOTHING
                """,
                (
                    month_start,
                    month_start.year,
                    month_start.month,
                    (month_start.month - 1) // 3 + 1,
                ),
            )
        seen_codes: set[str] = set()
        loaded = 0

        for line_number, row in enumerate(records, start=2):
            product_code = (row.get(columns["product_code"]) or "").strip()
            if not product_code:
                continue
            if product_code in seen_codes:
                raise ValueError(f"{path}:{line_number}: duplicate product code {product_code!r}.")
            seen_codes.add(product_code)
            product_name = (row.get(columns["product_name"]) or "").strip()
            if not product_name:
                raise ValueError(
                    f"{path}:{line_number}: product name is blank."
                )

            product_id = get_or_create_dimension(
                cursor, "dim_product", "product_code", product_code, "product_name"
            )
            location_code = (row.get(columns["location"]) or "").strip()
            if not location_code:
                raise ValueError(
                    f"{path}:{line_number}: location is blank."
                )
            location_id = get_or_create_dimension(
                cursor, "dim_location", "location_code", location_code
            )

            cursor.execute(
                """
                INSERT INTO fact_inventory_snapshot (
                    snapshot_date, product_id, supplier_id, location_id, batch_id,
                    stock_total, stock_local, retail_price, unit_cost,
                    sales_value_reported, total_units_sold_reported, margin_percent
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (snapshot_date, product_id, supplier_id, location_id)
                DO UPDATE SET
                    batch_id = EXCLUDED.batch_id,
                    stock_total = EXCLUDED.stock_total,
                    stock_local = EXCLUDED.stock_local,
                    retail_price = EXCLUDED.retail_price,
                    unit_cost = EXCLUDED.unit_cost,
                    sales_value_reported = EXCLUDED.sales_value_reported,
                    total_units_sold_reported = EXCLUDED.total_units_sold_reported,
                    margin_percent = EXCLUDED.margin_percent
                """,
                (
                    snapshot_date, product_id, supplier_id, location_id, batch_id,
                    parse_number(row.get(columns["stock_total"])),
                    parse_number(row.get(columns["stock_local"])),
                    parse_number(row.get(columns["retail_price"])),
                    parse_number(row.get(columns["unit_cost"])),
                    parse_number(row.get(columns["sales_value"])),
                    parse_number(row.get(columns["total_units_sold"])),
                    parse_number(row.get(columns["margin"])),
                ),
            )

            for month_header, month_start in periods:
                units = parse_number(row.get(month_header), blank=Decimal(0))
                cursor.execute(
                    """
                    INSERT INTO fact_monthly_sales
                        (month_start, product_id, supplier_id, location_id, batch_id, units_sold)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (month_start, product_id, supplier_id, location_id)
                    DO UPDATE SET batch_id = EXCLUDED.batch_id, units_sold = EXCLUDED.units_sold
                    """,
                    (month_start, product_id, supplier_id, location_id, batch_id, units),
                )
            loaded += 1
    return loaded


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-date",
        type=date.fromisoformat,
        required=True,
        help="Date of the stock snapshot (YYYY-MM-DD); not inferred from the CSV.",
    )
    parser.add_argument(
        "--sales-period-end",
        type=parse_month_end,
        required=True,
        help="Default month represented by the rightmost sales column (YYYY-MM).",
    )
    parser.add_argument(
        "--sales-period-end-for",
        action="append",
        type=parse_file_month,
        default=[],
        metavar="FILE=YYYY-MM",
        help="Override the sales period for a CSV whose rightmost month differs.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        help="PostgreSQL connection URL (or set DATABASE_URL).",
    )
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Stock CSV files; defaults to CSV files directly under files/labs_stock/.",
    )
    args = parser.parse_args()
    paths = args.files or sorted(DEFAULT_INPUT_DIRECTORY.glob("*.csv"))
    if not paths:
        parser.error(f"No stock CSV files found under {DEFAULT_INPUT_DIRECTORY}.")
    period_overrides = dict(args.sales_period_end_for)
    unknown_overrides = set(period_overrides) - {path.name for path in paths}
    if unknown_overrides:
        parser.error(
            "Period overrides do not match input files: "
            + ", ".join(sorted(unknown_overrides))
        )

    total = 0
    for path in paths:
        loaded = load_file(
            path,
            args.snapshot_date,
            period_overrides.get(path.name, args.sales_period_end),
            args.database_url,
        )
        total += loaded
        print(f"Loaded {path}: {loaded} product rows.")
    print(f"Import complete: {total} product rows across {len(paths)} file(s).")


if __name__ == "__main__":
    main()
