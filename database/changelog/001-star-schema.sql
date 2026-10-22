CREATE TABLE d_product (
    product_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_code TEXT NOT NULL UNIQUE,
    product_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE d_supplier (
    supplier_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_code TEXT NOT NULL UNIQUE,
    supplier_name TEXT NOT NULL
);

CREATE TABLE d_location (
    location_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    location_code TEXT NOT NULL UNIQUE
);

CREATE TABLE d_month (
    month_start DATE PRIMARY KEY,
    calendar_year SMALLINT NOT NULL,
    month_number SMALLINT NOT NULL CHECK (month_number BETWEEN 1 AND 12),
    quarter SMALLINT NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    UNIQUE (calendar_year, month_number)
);

CREATE TABLE etl_batch (
    batch_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_file TEXT NOT NULL,
    file_sha256 CHAR(64) NOT NULL,
    snapshot_date DATE NOT NULL,
    sales_period_end DATE NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (source_file, file_sha256, snapshot_date, sales_period_end)
);

CREATE TABLE f_inventory_snapshot (
    snapshot_date DATE NOT NULL,
    product_id BIGINT NOT NULL REFERENCES d_product(product_id),
    supplier_id BIGINT NOT NULL REFERENCES d_supplier(supplier_id),
    location_id BIGINT NOT NULL REFERENCES d_location(location_id),
    batch_id BIGINT NOT NULL REFERENCES etl_batch(batch_id),
    stock_total NUMERIC(14, 3),
    stock_local NUMERIC(14, 3),
    retail_price NUMERIC(14, 4),
    unit_cost NUMERIC(14, 4),
    sales_value_reported NUMERIC(16, 2),
    total_units_sold_reported NUMERIC(14, 3),
    margin_percent NUMERIC(8, 4),
    PRIMARY KEY (snapshot_date, product_id, supplier_id, location_id)
);

CREATE TABLE f_monthly_sales (
    month_start DATE NOT NULL REFERENCES d_month(month_start),
    product_id BIGINT NOT NULL REFERENCES d_product(product_id),
    supplier_id BIGINT NOT NULL REFERENCES d_supplier(supplier_id),
    location_id BIGINT NOT NULL REFERENCES d_location(location_id),
    batch_id BIGINT NOT NULL REFERENCES etl_batch(batch_id),
    units_sold NUMERIC(14, 3) NOT NULL,
    PRIMARY KEY (month_start, product_id, supplier_id, location_id)
);

CREATE INDEX f_inventory_product_date_idx
    ON f_inventory_snapshot (product_id, snapshot_date);
CREATE INDEX f_inventory_supplier_date_idx
    ON f_inventory_snapshot (supplier_id, snapshot_date);
CREATE INDEX f_sales_product_month_idx
    ON f_monthly_sales (product_id, month_start);
CREATE INDEX f_sales_supplier_month_idx
    ON f_monthly_sales (supplier_id, month_start);

CREATE VIEW inventory_changes AS
SELECT
    snapshot_date,
    product_id,
    supplier_id,
    location_id,
    stock_local,
    LAG(stock_local) OVER (
        PARTITION BY product_id, supplier_id, location_id
        ORDER BY snapshot_date
    ) AS previous_stock_local,
    stock_local - LAG(stock_local) OVER (
        PARTITION BY product_id, supplier_id, location_id
        ORDER BY snapshot_date
    ) AS stock_local_change
FROM fact_inventory_snapshot;
