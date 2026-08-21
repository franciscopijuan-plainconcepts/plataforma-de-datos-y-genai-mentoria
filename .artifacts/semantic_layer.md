# Semantic Layer — Plataforma de Datos y GenAI

- **Version**: `1.0.0`
- **Generated at**: `2026-08-21T07:17:46.172291+00:00`
- **Source SHA-256** (load manifest provenance): `d7a805f6cfd26339f478a3de6a6f128ea156179be093e09a1f528776fd5fdc3e`
- **Semantic source SHA-256** (`semantic_source.py` provenance): `e40350ee0d0b56a3b8c0cf0323650ee6217c83a530e5045dfc9ea429fddd0bba`

> Regeneratable via `uv run python -m src.cli.main generate-semantic-layer` (FR-008). The JSON artifact is deterministic (no `generated_at` field); this Markdown is for human reading.

## Assumptions

- returned_amount: A line is considered returned if at least one Returns row matches its Order ID. Because Returns."Order ID" has duplicates (multi-line returns), EXISTS is used instead of JOIN to avoid inflating rows.
- net_profit: Proportionally discounts total_profit by the fraction of sales that was returned (returned_amount / gross_sales * total_profit). Assumes returned lines have no independent profit record; a more precise subquery-based formula is documented for v3.0+.

## Tables

| Table | Type | Purpose |
| --- | --- | --- |
| `Orders` | fact | The transactional fact table of the Global Superstore Dataset: one row per order line, capturing sales, discount, profit, customer, product, and regional attributes. Central to the future v1.0/1.1 Text-to-SQL scope (primary query surface) and the v2.0 Semantic Layer (gross/net sales). |
| `Returns` | fact | Records which orders were returned. Linked to Orders via Order ID. Used in v2.0 to model business logic distinguishing net vs gross sales (a returned order's sales are subtracted from gross to compute net). |
| `People` | governance_mapping | Mapping of regional sales people / managers to the regions they govern. The basis for future v2.0 Row-Level Security: a user may only query data for regions assigned to them. |

## Metrics

### `gross_sales`

- **Business description**: Gross sales revenue across all order lines, before subtracting any returned revenue.
- **Aggregation**: `SUM`
- **Source table**: `Orders`
- **Uses Returns**: False
- **Formula (SQL)**:

```sql
SUM("Sales")
```

### `returned_amount`

- **Business description**: Total sales revenue of order lines that were returned (have a matching entry in the Returns table by Order ID).
- **Aggregation**: `EXPRESSION`
- **Source table**: `Orders`
- **Uses Returns**: True
- **Assumption**: A line is considered returned if at least one Returns row matches its Order ID. Because Returns."Order ID" has duplicates (multi-line returns), EXISTS is used instead of JOIN to avoid inflating rows.
- **Formula (SQL)**:

```sql
SUM(CASE WHEN EXISTS (SELECT 1 FROM Returns WHERE Returns."Order ID" = Orders."Order ID") THEN "Sales" ELSE 0 END)
```

### `net_sales`

- **Business description**: Net sales revenue: gross sales minus returned order-line revenue.
- **Aggregation**: `EXPRESSION`
- **Source table**: `Orders`
- **Uses Returns**: True
- **Derives from**: gross_sales, returned_amount
- **Formula (SQL)**:

```sql
SUM(CASE WHEN NOT EXISTS (SELECT 1 FROM Returns WHERE Returns."Order ID" = Orders."Order ID") THEN "Sales" ELSE 0 END)
```

### `return_rate`

- **Business description**: Fraction of gross sales that was returned: returned_amount / gross_sales.
- **Aggregation**: `RATIO`
- **Source table**: `Orders`
- **Uses Returns**: True
- **Derives from**: returned_amount, gross_sales
- **Formula (SQL)**:

```sql
CASE WHEN SUM("Sales") = 0 THEN NULL ELSE SUM(CASE WHEN EXISTS (SELECT 1 FROM Returns WHERE Returns."Order ID" = Orders."Order ID") THEN "Sales" ELSE 0 END) / SUM("Sales") END
```

### `total_profit`

- **Business description**: Sum of signed profit across all order lines (negatives are losses).
- **Aggregation**: `SUM`
- **Source table**: `Orders`
- **Uses Returns**: False
- **Formula (SQL)**:

```sql
SUM("Profit")
```

### `net_profit`

- **Business description**: Net profit: total profit minus the proportion of profit corresponding to returned order lines.
- **Aggregation**: `EXPRESSION`
- **Source table**: `Orders`
- **Uses Returns**: True
- **Derives from**: total_profit, returned_amount
- **Assumption**: Proportionally discounts total_profit by the fraction of sales that was returned (returned_amount / gross_sales * total_profit). Assumes returned lines have no independent profit record; a more precise subquery-based formula is documented for v3.0+.
- **Formula (SQL)**:

```sql
SUM("Profit") - (CASE WHEN SUM("Sales") = 0 THEN 0 ELSE (SUM(CASE WHEN EXISTS (SELECT 1 FROM Returns WHERE Returns."Order ID" = Orders."Order ID") THEN "Sales" ELSE 0 END) / SUM("Sales")) * SUM("Profit") END)
```

### `avg_order_value`

- **Business description**: Average value of a distinct order: gross sales divided by the number of distinct Order IDs.
- **Aggregation**: `EXPRESSION`
- **Source table**: `Orders`
- **Uses Returns**: False
- **Derives from**: gross_sales
- **Formula (SQL)**:

```sql
SUM("Sales") / NULLIF(COUNT(DISTINCT "Order ID"), 0)
```

### `order_count`

- **Business description**: Number of distinct orders (by Order ID).
- **Aggregation**: `COUNT_DISTINCT`
- **Source table**: `Orders`
- **Uses Returns**: False
- **Formula (SQL)**:

```sql
COUNT(DISTINCT "Order ID")
```

## Dimensions

### Geographic

- `region` (`Region`) — Geographic region of the order. Shared with Returns and People — the basis for RLS.
- `country` (`Country`) — Country of the delivery address.
- `market` (`Market`) — Top-level market the order belongs to (Asia Pacific, Europe, Africa, LATAM, USCA).

### Categorical

- `segment` (`Segment`) — Customer segment (consumer, corporate, home office).
- `category` (`Category`) — Top-level product category (Furniture, Office Supplies, Technology).
- `sub_category` (`Sub-Category`) — Product sub-category (e.g., Binders, Chairs, Phones).
- `ship_mode` (`Ship Mode`) — Shipping class selected for the order.
- `order_priority` (`Order Priority`) — Priority level of the order (Low, Medium, High, Critical).
- `customer` (`Customer Name`) — Name of the customer who placed the order.
- `product` (`Product Name`) — Human-readable product name.

### Temporal

- `order_date` (`Order Date`) — Date the order was placed.

## Relationships

### `orders_to_returns`

- `Returns.Order ID` → `Orders.Order ID` (N:1, LEFT JOIN)
- **Notes**: Returns."Order ID" has 63 duplicate values across 2,033 rows (multi-line returns) — use EXISTS instead of direct JOIN to avoid inflating Orders rows.

### `orders_to_people_by_region`

- `People.Region` → `Orders.Region` (1:N, INNER JOIN)
- **Notes**: Taxonomy mismatch: People splits Canada into Eastern/Western Canada vs Orders' single Canada (23 unique). Resolution is v3.0+ scope; the resolver is conservative — a viewer scoped to 'Eastern Canada' sees 0 Orders rows.
