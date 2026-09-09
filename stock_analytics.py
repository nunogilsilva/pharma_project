"""Analyse product stock against historical monthly sales."""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).parent
DEFAULT_CSV = ROOT / "files" / "labs_stock" / "atral_setembro.csv"
DEFAULT_MAP = ROOT / "column_map.json"
DEFAULT_OUTPUT = ROOT / "files" / "labs_stock" / "analytics_results" / "atral_setembro_2_meses.csv"


def load_map(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def find_column(dataframe: pd.DataFrame, aliases: list[str], field: str) -> str:
    normalized = {str(column).strip().casefold(): str(column) for column in dataframe}
    for alias in aliases:
        if alias.casefold() in normalized:
            return normalized[alias.casefold()]
    raise ValueError(f"Could not map '{field}'. Tried: {', '.join(aliases)}")


def find_month_columns(dataframe: pd.DataFrame, config: dict[str, Any]) -> list[str]:
    names = {name.casefold() for name in config["base_names"]}
    separator = re.escape(config.get("suffix_separator", "_"))
    pattern = re.compile(
        rf"^({'|'.join(map(re.escape, names))})(?:{separator}(?:\d+)?)?$",
        re.I,
    )
    return [str(column) for column in dataframe if pattern.match(str(column).strip())]


def find_recent_month_columns(
    dataframe: pd.DataFrame,
    month_columns: list[str],
    months_to_use: int,
) -> list[str]:
    """Return latest completed months, excluding the current rightmost month."""
    completed_months = month_columns[:-1]
    monthly = dataframe[completed_months].apply(numeric_series, axis=0)
    completed = [
        column for column in completed_months if monthly[column].notna().any()
    ]
    return completed[-months_to_use:]


def numeric_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    cleaned = (
        series.astype("string")
        .str.replace(r"[^\d,.\-]", "", regex=True)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def analyse(
    dataframe: pd.DataFrame,
    mapping: dict[str, Any],
    months_to_cover: float | None = None,
) -> pd.DataFrame:
    column_config = mapping["columns"]
    product_code = find_column(dataframe, column_config["product_code"], "product_code")
    product_name = find_column(dataframe, column_config["product_name"], "product_name")
    stock_column = find_column(dataframe, column_config["stock_local"], "stock_local")
    month_columns = find_month_columns(dataframe, mapping["month_columns"])
    if not month_columns:
        raise ValueError("No monthly sales columns were found in the CSV.")
    settings = mapping["recommendation"].copy()
    if months_to_cover is not None:
        if months_to_cover <= 0:
            raise ValueError("months_to_cover must be greater than zero.")
        settings["months_to_cover"] = months_to_cover
    recent_month_columns = find_recent_month_columns(
        dataframe,
        month_columns,
        settings["recent_months"],
    )
    if not recent_month_columns:
        raise ValueError("No completed monthly sales columns were found in the CSV.")

    result = pd.DataFrame(
        {
            "product_code": dataframe[product_code],
            "product_name": dataframe[product_name],
            "stock": numeric_series(dataframe[stock_column]),
        }
    )
    monthly = dataframe[month_columns].apply(numeric_series, axis=0).fillna(0)
    recent_monthly = monthly[recent_month_columns]
    result["recent_months"] = ", ".join(recent_month_columns)
    result["recent_months_with_sales"] = (recent_monthly > 0).sum(axis=1)
    result["recent_units_sold"] = recent_monthly.sum(axis=1)
    result["recent_average_monthly_sales"] = recent_monthly.mean(axis=1)
    result["recent_sales_stddev"] = recent_monthly.std(axis=1, ddof=0).fillna(0)
    result["months_with_data"] = (monthly > 0).sum(axis=1)
    result["annual_units_sold"] = monthly.sum(axis=1)
    result["average_monthly_sales"] = monthly.mean(axis=1)
    result["sales_stddev"] = monthly.std(axis=1, ddof=0).fillna(0)

    enough_recent_data = len(recent_month_columns) >= settings["minimum_months_required"]
    sold_recently = result["recent_units_sold"] > 0
    safety_stock = settings["service_level_z"] * result["recent_sales_stddev"]
    target_stock = (
        result["recent_average_monthly_sales"] * settings["months_to_cover"]
        + safety_stock
    )
    stock_covers_recent_demand = (
        result["stock"].fillna(0) >= result["recent_average_monthly_sales"]
    )
    no_stock_low_sales = (
        (result["stock"].fillna(0) <= 0)
        & (result["recent_average_monthly_sales"] < 0.7)
        & (result["average_monthly_sales"] <= 0.8)
    )
    result["recommended_order"] = (
        (target_stock - result["stock"].fillna(0))
        .clip(lower=0)
        .apply(math.ceil)
    )
    result.loc[
        (not enough_recent_data)
        | ~sold_recently
        | stock_covers_recent_demand
        | no_stock_low_sales,
        "recommended_order",
    ] = 0
    result["product_code"] = result["product_code"].astype("string").str.strip()
    result = result[
        result["product_code"].notna()
        & numeric_series(result["product_code"]).notna()
    ]
    return result.sort_values("product_name", ascending=False, na_position="last")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", nargs="?", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--map", dest="map_file", type=Path, default=DEFAULT_MAP)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output CSV path (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--months-to-cover",
        type=float,
        help=(
            "Number of months the recommended order should cover. "
            "Overrides the value in column_map.json."
        ),
    )
    args = parser.parse_args()

    dataframe = pd.read_csv(args.csv_file, encoding="utf-8-sig")
    result = analyse(
        dataframe,
        load_map(args.map_file),
        months_to_cover=args.months_to_cover,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(args.output, index=False, encoding="utf-8-sig")
    print(f"Saved analysis to: {args.output}")


if __name__ == "__main__":
    main()
