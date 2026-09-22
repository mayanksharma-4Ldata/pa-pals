# Copy this file to snowflake_config.py and fill in your credentials.
# snowflake_config.py is git-ignored and should never be committed.

SNOWFLAKE = dict(
    account   = 'YOUR_ACCOUNT',          # e.g. cua77229
    user      = 'YOUR_USER',             # e.g. etlAdmin@yourorg.com
    password  = 'YOUR_PASSWORD',
    warehouse = 'YOUR_WAREHOUSE',        # e.g. 4L_ETL_WH
    database  = 'dw_pdmpi',
    schema    = 'ui',
    role      = 'YOUR_ROLE',             # e.g. 4L_READWRITE_ROLE
)

DB              = 'dw_pdmpi'
SCHEMA          = 'ui'
PIMASTER        = f'{DB}.{SCHEMA}.pimaster'
PILICENSEMASTER = f'{DB}.{SCHEMA}.pilicensemaster'
