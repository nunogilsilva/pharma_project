# CSV file columns

Código - Product Code
Designação - Product Name and description
Stk. Tot - Total Stock
Local - 
Stk. Loc - Current Stock
P.V.P. - Retail Price
V. Vendas - Sales Value 
P.Custo - Cost Price
MG % - Margin
JAN - January
FEV - February
MAR - March
ABR - April
MAI - May
JUN - June
JUL - July
AGO - August
SET - September
OUT - October
NOV - November
DEZ - December
TUnd - Total Units
# Stock analysis

## PostgreSQL stock warehouse

The existing desktop tools and CSV workflows remain unchanged. The optional
warehouse in `database/` adds a PostgreSQL star schema governed by Liquibase:

- `dim_product`, `dim_supplier`, `dim_location`, and `dim_month` describe the
  product, source lab/export, pharmacy location, and sales month.
- `fact_inventory_snapshot` stores inventory and reported price/summary
  measures at the supplied snapshot date.
- `fact_monthly_sales` stores units sold at product, supplier, location, and
  month grain. `inventory_changes` compares each inventory snapshot with the
  preceding snapshot.

Start PostgreSQL and apply the Liquibase changelog from the repository root:

```bash
docker compose -f database/compose.yaml up -d postgres
docker compose -f database/compose.yaml --profile governance run --rm liquibase
```

The compose file uses local development credentials (`pharma` / `pharma`) and
a named volume. Set `DATABASE_URL` to use another PostgreSQL instance.

Load the stock CSVs in `files/labs_stock/`:

```bash
# Replace these example periods with the dates represented by your exports.
uv sync
uv run python database/load_stock_data.py \
  --snapshot-date 2026-10-21 \
  --sales-period-end 2026-10 \
  --sales-period-end-for barral.csv=2026-09
```

Supply the actual date represented by the stock report and the month represented
by each file's rightmost sales column; these dates are not present in the CSV
and are never guessed by the loader. Use `--sales-period-end-for FILE=YYYY-MM`
when an individual file ends in a different month. The rightmost header must
match its supplied sales-period month, and all monthly headers are checked for
consecutive months. The loader can also take explicit CSV paths after the
options. Re-importing the same source at the same dates updates the existing
facts rather than duplicating them.

## Offline desktop application

The graphical application runs locally on macOS and Windows. It does not
upload files or require a server. The first launch asks you to create a local
password; subsequent launches require that password.

Install dependencies and launch it with:

```bash
uv sync
uv run python pharma_app.py
```

The application supports:

- converting one or more `.xls`/`.xlsx` reports to CSV;
- running stock analysis for a selected CSV or Excel report;
- running top molecule sales for selected molecule CSV files;
- saving each run in a separate local job folder.

Generated files are stored under the operating system's application-data
directory in `Pharma Analytics/jobs`.

## Convert the report

```bash
uv run python csv_converter.py
```

This uses the original stock-report parser, reads the configured Sandoz
workbook, compacts it into one product per row, and writes a CSV beside it.
That parser is kept separate from the molecule-report parser.

## Convert the molecule report

```bash
uv run python csv_converter.py files/molecule/genericos.xls \
  --format generic \
  --output files/molecule/genericos.csv
```

The `generic` format handles the different row layout in `genericos.xls`,
including product data that can appear on the same row as the product code and
the report's summary section. Use `--format stock` for the original Sandoz
structure; it remains the default for backward compatibility.

## Analyse stock

```bash
uv run python stock_analytics.py
```

The analysis reads `files/sandoz.csv` and prints products with a recommended
order quantity. To save the result:

```bash
uv run python stock_analytics.py --output files/stock_analysis.csv
```

The default order coverage is configured by `recommendation.months_to_cover`
in `column_map.json`. It can be overridden for a single run, for example:

```bash
uv run python stock_analytics.py --months-to-cover 2
```

Column aliases and month detection are kept in `column_map.json`, so renamed
columns can be supported without changing the analysis code. Month columns are
read in the order they appear in the CSV: the rightmost month is treated as
the current, possibly incomplete month and excluded, so the months before it
are the most recent completed ones. The recommendation targets the configured
number of months of recent average sales plus
`1.65` standard deviations of recent monthly demand. Products with no sales in
the latest three completed months are assigned an order of zero. An order is
also suppressed when current local stock already covers the recent average
monthly sales. Adjust these settings in the map file.
Products with no local stock are also assigned an order of zero when recent
average monthly sales are below `0.7` and overall average monthly sales are at
most `0.8`.

## Top molecule sales

To show the 30 molecule products with the highest `TUnd` total:

```bash
uv run python top_molecule_sales.py
```

The script reads every CSV file in `files/molecule/`, groups products by the
first word of `Designação`, sums `TUnd` for each group, and saves the result to
`files/molecule/analytics_results/top_molecule_sales.csv`. Product codes and
source files contributing to each group are retained in the output. A
second file,
`files/molecule/analytics_results/top_molecule_sales_details.csv`, lists every
individual `Designação`, product code, and `TUnd` belonging to those top groups.
A different directory, result count, output path, detailed output path, or
configuration file can be supplied.
Excluded `Designação` prefixes are configured in
`top_molecule_sales_config.json` and are matched case-insensitively:

```bash
uv run python top_molecule_sales.py path/to/csvs \
  --limit 30 \
  --output path/to/top_molecule_sales.csv \
  --detail-output path/to/top_molecule_sales_details.csv \
  --config top_molecule_sales_config.json
```