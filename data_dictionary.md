# Data Dictionary — Plataforma de Datos y GenAI

- **Source file**: `/home/franciscopijuan/PROJECTS/MENTORIA/Plataforma_de_Datos_y_GenAI/Global Superstore Data.xlsx`
- **Source SHA-256**: `d7a805f6cfd26339f478a3de6a6f128ea156179be093e09a1f528776fd5fdc3e`
- **Generated at**: 2026-07-30T15:52:09.721840+00:00
- **Tables**: `Orders` (Transactional Logs), `Returns` (Reverse Logistics), `People` (Sales Governance)

> This dictionary integrates Kaggle semantic descriptions with EDA-derived database types. Regeneratable via `uv run python -m src.cli.main generate-dictionary` (FR-012).

## `Orders` — Transactional Logs

**Purpose**: The transactional fact table of the Global Superstore Dataset: one row per order line, capturing sales, discount, profit, customer, product, and regional attributes. Central to the future v1.0/1.1 Text-to-SQL scope (primary query surface) and the v2.0 Semantic Layer (gross/net sales).

**Primary key**: Row ID

| Column | Business description | Type | Null | Key | Allowed values | Min/Max | Unique | Data-quality notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `Row ID` | Unique identifier for each order line (one row per line item within an order). | INTEGER (INTEGER) | NOT NULL | primary | — | 1 … 51290 | 51290 | — |
| `Order ID` | Identifier for the order; one order spans multiple line items, so this is not unique per row. | VARCHAR(50) (STRING) | NOT NULL | — | — | — | 25728 | — |
| `Order Date` | Date the order was placed. | TIMESTAMP (TIMESTAMP) | NOT NULL | — | — | 2014-01-01 00:00:00 … 2017-12-31 00:00:00 | 1429 | — |
| `Ship Date` | Date the order was shipped (always on or after Order Date). | TIMESTAMP (TIMESTAMP) | NOT NULL | — | — | 2014-01-03 00:00:00 … 2018-01-07 00:00:00 | 1463 | — |
| `Ship Mode` | Shipping class selected for the order. | VARCHAR(20) (STRING) | NOT NULL | — | Standard Class, Second Class, Standard Class, Standard Class, Second Class | — | 4 | — |
| `Customer ID` | Unique identifier for the customer who placed the order. | VARCHAR(50) (STRING) | NOT NULL | — | — | — | 17415 | — |
| `Customer Name` | Name of the customer who placed the order. | VARCHAR(100) (STRING) | NOT NULL | — | — | — | 796 | — |
| `Segment` | Customer segment (consumer, corporate, or home office). | VARCHAR(20) (STRING) | NOT NULL | — | Home Office, Consumer, Home Office, Home Office, Consumer | — | 3 | — |
| `Postal Code` | Postal/ZIP code of the delivery address. Only populated for US/Canada rows (80% NULL). | VARCHAR(20) (STRING) | NULL | — | — | 1040.0 … 99301.0 | 631 | 80% NULL — only US/Canada rows have postal codes; nullable. |
| `City` | City of the delivery address. | VARCHAR(100) (STRING) | NOT NULL | — | — | — | 3650 | — |
| `State` | State/province of the delivery address. | VARCHAR(100) (STRING) | NOT NULL | — | — | — | 1106 | — |
| `Country` | Country of the delivery address. | VARCHAR(100) (STRING) | NOT NULL | — | — | — | 165 | — |
| `Region` | Geographic region of the order. Shared with Returns and People — the basis for future v2.0 Row-Level Security. | VARCHAR(50) (STRING) | NOT NULL | foreign | Southern Asia, Southern Asia, Southern Asia, Southern Asia, Southern Asia | — | 23 | Region taxonomy mismatch with People (see People.Region note); 22 of 24 People regions overlap with Orders. Kept as VARCHAR pending v2.0 Semantic-Layer consolidation. |
| `Market` | Top-level market the order belongs to (Asia Pacific, Europe, Africa, LATAM, USCA). | VARCHAR(20) (STRING) | NOT NULL | — | Asia Pacific, Asia Pacific, Asia Pacific, Asia Pacific, Asia Pacific | — | 5 | — |
| `Product ID` | Unique identifier for the product ordered. | VARCHAR(50) (STRING) | NOT NULL | — | — | — | 3788 | — |
| `Product Name` | Human-readable product name. | VARCHAR(300) (STRING) | NOT NULL | — | — | — | 3788 | — |
| `Sub-Category` | Product sub-category (e.g., Binders, Chairs, Phones). | VARCHAR(30) (STRING) | NOT NULL | — | Bookcases, Supplies, Machines, Furnishings, Envelopes | — | 17 | — |
| `Category` | Top-level product category (Furniture, Office Supplies, Technology). | VARCHAR(30) (STRING) | NOT NULL | — | Furniture, Office Supplies, Technology, Furniture, Office Supplies | — | 3 | — |
| `Sales` | Gross sales revenue for the line item, before returns. Ingredient for v2.0 gross/net sales logic. | NUMERIC(12,4) (DECIMAL) | NOT NULL | — | — | 0.444 … 22638.48 | 22995 | — |
| `Quantity` | Number of units ordered for the line item. | INTEGER (INTEGER) | NOT NULL | — | — | 1 … 14 | 14 | — |
| `Discount` | Fractional discount applied to the line item (0.0–0.85). Ingredient for net-vs-gross computation in v2.0. | NUMERIC(5,4) (DECIMAL) | NOT NULL | — | — | 0.0 … 0.85 | 27 | Fractional amount 0.0–0.85; contains non-round values like 0.402, 0.002 (kept as-is). |
| `Profit` | Profit for the line item; signed (negative values represent losses). Ingredient for v2.0 business logic. | NUMERIC(12,4) (DECIMAL) | NOT NULL | — | — | -6599.978 … 8399.976 | 24575 | Signed — negative values allowed (losses). |
| `Shipping Cost` | Cost to ship the line item. | NUMERIC(10,4) (DECIMAL) | NOT NULL | — | — | 1.002 … 933.57 | 16452 | — |
| `Order Priority` | Priority level of the order (Low, Medium, High, Critical). | VARCHAR(20) (STRING) | NOT NULL | — | Medium, Medium, Medium, Medium, Medium | — | 4 | — |

**Relationships**:
- `Region` → `People.Region` (N:1)

## `Returns` — Reverse Logistics

**Purpose**: Records which orders were returned. Linked to Orders via Order ID. Used in v2.0 to model business logic distinguishing net vs gross sales (a returned order's sales are subtracted from gross to compute net).

**Primary key**: Return ID

| Column | Business description | Type | Null | Key | Allowed values | Min/Max | Unique | Data-quality notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `Returned` | Flag indicating the order was returned. Degenerate: always 'Yes' in the source; the row's presence itself encodes 'returned'. | VARCHAR(5) (STRING) | NOT NULL | — | Yes, Yes, Yes, Yes, Yes | — | 1 | Degenerate column — always 'Yes'; the row's presence encodes 'returned'. |
| `Order ID` | Identifier of the returned order; links to Orders.Order ID. Not unique (multi-line returns produce duplicates). | VARCHAR(50) (STRING) | NOT NULL | foreign | — | — | 1970 | 63 duplicate values across 2,033 rows — multi-line returns; Order ID is NOT the PK (surrogate Return ID is). |
| `Region` | Geographic region of the returned order. Shared with Orders and People — basis for future v2.0 RLS. | VARCHAR(50) (STRING) | NOT NULL | foreign | Southern Asia, Southern Asia, Southern Asia, North Africa, North Africa | — | 23 | — |
| `Return ID` | Surrogate primary key assigned at load (row-ordinal). Introduced because Order ID has duplicates (multi-line returns). | SERIAL (INTEGER) | NOT NULL | primary | — | — | 2033 | — |

**Relationships**:
- `Order ID` → `Orders.Order ID` (N:1)
- `Region` → `People.Region` (N:1)

## `People` — Sales Governance

**Purpose**: Mapping of regional sales people / managers to the regions they govern. The basis for future v2.0 Row-Level Security: a user may only query data for regions assigned to them.

**Primary key**: Person

| Column | Business description | Type | Null | Key | Allowed values | Min/Max | Unique | Data-quality notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `Person` | Name of the regional sales person / manager responsible for a region. Primary key. Normalized at load (non-breaking spaces stripped). | VARCHAR(100) (STRING) | NOT NULL | primary | Marilène Rousseau, Andile Ihejirika, Nicodemo Bautista, Cansu Peynirci, Lon Bonher | — | 24 | Normalized at load: non-breaking spaces (\xa0) replaced with regular spaces. |
| `Region` | Region the person governs. Shared with Orders/Returns — basis for future v2.0 Row-Level Security (a user may only see data for their assigned regions). | VARCHAR(50) (STRING) | NOT NULL | foreign | Caribbean, Central Africa, Central America, Central Asia, Central US | — | 24 | Region taxonomy mismatch — People splits Canada into Eastern/Western Canada (24 regions) vs Orders' single Canada (23 regions); 22 of 24 overlap. Kept as VARCHAR, not enum — resolution is v2.0 Semantic-Layer scope. |

**Relationships**:
- _(none — this table is a root in the schema graph)_

---

## Cross-Table Relationships (overview)

- **`Returns.Order ID` → `Orders.Order ID`** (N:1) — a return refers to an order.
- **`People.Region` → `Orders.Region`** (1:N) — regional sales-person governs orders.
- **`People.Region` → `Returns.Region`** (1:N) — regional governance applies to returns.

> Row-Level Security on `People`/`Region` is **v2.0 scope** and is NOT enforced at this baseline. The data model preserves the `Region` columns needed for it.
