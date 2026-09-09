"""Report the 30 molecule products with the highest total units sold."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).parent
DEFAULT_DIRECTORY = Path(__file__).parent / "files" / "molecule"
DEFAULT_OUTPUT = DEFAULT_DIRECTORY / "analytics_results" / "top_molecule_sales.csv"
DEFAULT_DETAIL_OUTPUT = (
    DEFAULT_DIRECTORY / "analytics_results" / "top_molecule_sales_details.csv"
)
DEFAULT_CONFIG = ROOT / "top_molecule_sales_config.json"
DEFAULT_LIMIT = 30


def numeric_series(series: pd.Series) -> pd.Series:
    """Convert numeric values, including European-formatted text, to numbers."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")
    cleaned = (
        series.astype("string")
        .str.replace(r"[^\d,.\-]", "", regex=True)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    return pd.to_numeric(cleaned, errors="coerce")


def load_config(path: Path) -> dict[str, Any]:
    """Read the editable top-sales analysis configuration."""
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def load_molecule_csvs(directory: Path) -> pd.DataFrame:
    """Read all molecule CSV files and retain their source file."""
    csv_files = sorted(directory.glob("*.csv"))
    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in: {directory}")

    frames: list[pd.DataFrame] = []
    for csv_file in csv_files:
        dataframe = pd.read_csv(csv_file, encoding="utf-8-sig")
        if "TUnd" not in dataframe.columns:
            raise ValueError(f"Missing 'TUnd' column in: {csv_file}")
        dataframe["TUnd"] = numeric_series(dataframe["TUnd"])
        dataframe["source_file"] = csv_file.name
        frames.append(dataframe)

    return pd.concat(frames, ignore_index=True)


def apply_exclusions(
    dataframe: pd.DataFrame,
    excluded_prefixes: list[str] | None,
) -> pd.DataFrame:
    """Remove rows whose designation starts with a configured prefix."""
    if excluded_prefixes:
        if "Designação" not in dataframe.columns:
            raise ValueError("Missing 'Designação' column in molecule CSV files.")
        prefixes = tuple(prefix.casefold() for prefix in excluded_prefixes)
        designations = dataframe["Designação"].astype("string").str.casefold()
        dataframe = dataframe[~designations.str.startswith(prefixes, na=False)]
    return dataframe


def add_product_group(dataframe: pd.DataFrame) -> pd.DataFrame:
    """Add the first-word product group used by the ranking."""
    dataframe = dataframe.dropna(subset=["TUnd", "Designação"]).copy()
    dataframe["product_group"] = (
        dataframe["Designação"].astype("string").str.strip().str.split().str[0]
    )
    return dataframe


def top_sold_products(
    directory: Path,
    limit: int = DEFAULT_LIMIT,
    excluded_prefixes: list[str] | None = None,
) -> pd.DataFrame:
    """Return product-name groups ranked by summed total units sold."""
    if limit <= 0:
        raise ValueError("limit must be greater than zero.")

    dataframe = add_product_group(
        apply_exclusions(load_molecule_csvs(directory), excluded_prefixes)
    )
    grouped = (
        dataframe.groupby("product_group", sort=False)
        .agg(
            **{
                "Código": (
                    "Código",
                    lambda values: ", ".join(
                        dict.fromkeys(str(value) for value in values if pd.notna(value))
                    ),
                ),
                "Designação": ("product_group", "first"),
                "TUnd": ("TUnd", "sum"),
                "source_file": (
                    "source_file",
                    lambda values: ", ".join(dict.fromkeys(values)),
                ),
            }
        )
        .reset_index(drop=True)
        .sort_values(
            ["TUnd", "Designação"],
            ascending=[False, True],
            na_position="last",
        )
    )
    return grouped[["Código", "Designação", "TUnd", "source_file"]].head(
        limit
    ).reset_index(drop=True)


def top_sold_product_details(
    directory: Path,
    top_groups: pd.DataFrame,
    excluded_prefixes: list[str] | None = None,
) -> pd.DataFrame:
    """Return individual product rows belonging to the ranked groups."""
    dataframe = add_product_group(
        apply_exclusions(load_molecule_csvs(directory), excluded_prefixes)
    )
    groups = set(top_groups["Designação"])
    details = dataframe[dataframe["product_group"].isin(groups)].copy()
    details = details.sort_values(
        ["product_group", "TUnd"],
        ascending=[True, False],
    )
    return details[["product_group", "Código", "Designação", "TUnd", "source_file"]]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show the molecule products with the highest TUnd."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=DEFAULT_DIRECTORY,
        help=f"Directory containing molecule CSV files (default: {DEFAULT_DIRECTORY}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Number of products to show (default: {DEFAULT_LIMIT}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output CSV path (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--detail-output",
        type=Path,
        default=DEFAULT_DETAIL_OUTPUT,
        help=f"Detailed output CSV path (default: {DEFAULT_DETAIL_OUTPUT}).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"JSON configuration path (default: {DEFAULT_CONFIG}).",
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
        excluded_prefixes = config.get("excluded_designation_prefixes", [])
        if not isinstance(excluded_prefixes, list) or not all(
            isinstance(prefix, str) for prefix in excluded_prefixes
        ):
            raise ValueError(
                "'excluded_designation_prefixes' must be a list of strings."
            )
        result = top_sold_products(
            args.directory,
            args.limit,
            excluded_prefixes,
        )
    except (
        FileNotFoundError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        pd.errors.ParserError,
    ) as error:
        parser.error(str(error))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    details = top_sold_product_details(
        args.directory,
        result,
        excluded_prefixes,
    )
    result.to_csv(args.output, index=False, encoding="utf-8-sig")
    args.detail_output.parent.mkdir(parents=True, exist_ok=True)
    details.to_csv(args.detail_output, index=False, encoding="utf-8-sig")
    print(f"Saved results to: {args.output}\n")
    print(f"Saved detailed results to: {args.detail_output}\n")


if __name__ == "__main__":
    main()
