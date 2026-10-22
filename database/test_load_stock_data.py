import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from database.load_stock_data import (
    assign_months,
    month_columns,
    parse_number,
    required_column,
    read_stock_csv,
)


class StockLoaderTests(unittest.TestCase):
    def test_assign_months_across_year_boundary(self) -> None:
        columns = [("NOV", 11), ("DEZ", 12), ("JAN", 1), ("FEV", 2)]
        self.assertEqual(
            assign_months(columns, date(2026, 2, 1)),
            [
                ("NOV", date(2025, 11, 1)),
                ("DEZ", date(2025, 12, 1)),
                ("JAN", date(2026, 1, 1)),
                ("FEV", date(2026, 2, 1)),
            ],
        )

    def test_assign_months_rejects_mismatch(self) -> None:
        with self.assertRaisesRegex(ValueError, "not consecutive"):
            assign_months([("JAN", 1), ("MAR", 3)], date(2026, 3, 1))
        with self.assertRaisesRegex(ValueError, "rightmost sales column"):
            assign_months([("JAN", 1)], date(2026, 2, 1))

    def test_parse_localized_decimal_values(self) -> None:
        self.assertEqual(parse_number("5,95€"), Decimal("5.95"))
        self.assertEqual(parse_number("8.884,89€"), Decimal("8884.89"))
        self.assertEqual(parse_number("2.0"), Decimal("2.0"))
        self.assertEqual(parse_number("", blank=Decimal(0)), Decimal(0))

    def test_current_stock_files_have_mapped_columns_and_contiguous_months(self) -> None:
        input_dir = Path(__file__).resolve().parents[1] / "files" / "labs_stock"
        for path in input_dir.glob("*.csv"):
            with self.subTest(file=path.name):
                headers, _ = read_stock_csv(path)
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
                ):
                    required_column(headers, field)
                months = month_columns(headers)
                self.assertEqual(len(months), 15)
                assign_months(months, date(2026, months[-1][1], 1))


if __name__ == "__main__":
    unittest.main()
