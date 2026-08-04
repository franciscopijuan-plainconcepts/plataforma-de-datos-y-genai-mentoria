"""Data Engineering domain — ingestion pipelines, EDA, dictionary, validation.

The only domain implemented in this baseline. Depends on `src/contracts/`
(shared downward-dependency layer) and the data-access Protocols in
`src/data_access/interfaces.py`. MUST NOT import any engine-specific
library (psycopg, google-cloud-bigquery) directly — those live only inside
`src/data_access/adapters/<engine>/` (constitution Principle II & III).
"""
