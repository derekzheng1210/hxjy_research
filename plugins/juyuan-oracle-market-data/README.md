# Juyuan Oracle Market Data

Read-only MCP plugin for the local FinChina/Juyuan Oracle database. It exposes ChinaBond yield curves and ChinaBond bond valuation yields; it never writes to Oracle.

## Installation prerequisites

The checked-in MCP configuration uses this project's `.venv`. Install the plugin dependencies there:

```powershell
.\.venv\Scripts\python.exe -m pip install -r .\plugins\juyuan-oracle-market-data\requirements.txt
```

Set these environment variables in the environment that starts Codex (or make them available to the MCP server):

```text
JUYUAN_DB_USER
JUYUAN_DB_PASSWORD
JUYUAN_DB_DSN
```

`JUYUAN_ORACLE_CLIENT` is optional. Set it to the Oracle Instant Client directory when the thin driver cannot connect in the local network environment.

## Tools

- `get_latest_market_dates`: return the most recent available curve and valuation dates.
- `get_yield_curve`: return selected curve points for a date or a date range. Historical reads are split into 31-calendar-day chunks and use fixed curve codes.
- `get_cnbd_valuations`: return bond valuation yields from `TQ_QT_CBESTIMATE` for one date.

## Data definitions

Yield curves use `TQ_QT_YIELDCURVE`, `YCURVETYPE='1'`, and `ISVALID=1`. The plugin uses fixed production codes for common credit strategy curves and only reads requested maturities. This avoids expensive name discovery and unrestricted date scans.

Bond valuations use `TQ_QT_CBESTIMATE` joined to `TQ_BD_NEWESTBASICINFO`, with `DATASOURCE='1'` and `ISVALID=1`. For a bond with more than one valid ChinaBond row, `VALUATIONTYPE='1'` is preferred; otherwise the first remaining ChinaBond row is used. `TQ_BD_SHCLEST` is not used because it represents Shanghai Clearing House valuations rather than ChinaBond valuations.
