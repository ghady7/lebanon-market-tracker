import io
import logging
import os
import pathlib
import re
import sys
import time
import urllib.parse
from datetime import datetime
from typing import Final, List, Optional

import pandas as pd
import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from sqlalchemy import create_engine, text
from urllib3.util.retry import Retry

# 1. Project paths & Environment setup
BASE_DIR: Final = pathlib.Path(__file__).resolve().parent.parent
SQL_DIR: Final = BASE_DIR / "sql"
LOGS_DIR: Final = BASE_DIR / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(BASE_DIR / ".env", override=False)

SERVER = os.getenv("SQL_SERVER", "localhost")
DATABASE = os.getenv("SQL_DATABASE", "lebanon_ecom")
DRIVER = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
WFP_URL = os.getenv(
    "WFP_CSV_URL",
    "https://data.humdata.org/dataset/db0b4fb1-ce11-458e-94e0-3342365a117a/"
    "resource/772bc34e-1327-4ada-b2eb-e72020a546f2/download/"
    "wfp_food_prices_lbn.csv",
)
REQUEST_TIMEOUT = int(os.getenv("API_TIMEOUT_SECONDS", "60"))
STAGE_TABLES: Final = {
    "staging_wfp_prices",
    "staging_world_bank",
}

# 2. Configure Logging (Console + File)
LOGGER = logging.getLogger("lebanon_market_pipeline")


def setup_logging():
    LOGGER.setLevel(logging.INFO)
    LOGGER.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console Handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    LOGGER.addHandler(ch)

    # File Handlers (History log + Latest run log)
    run_log_file = LOGS_DIR / f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"
    latest_log_file = LOGS_DIR / "pipeline_latest.log"

    fh_daily = logging.FileHandler(run_log_file, encoding="utf-8")
    fh_daily.setLevel(logging.INFO)
    fh_daily.setFormatter(formatter)
    LOGGER.addHandler(fh_daily)

    fh_latest = logging.FileHandler(latest_log_file, mode="w", encoding="utf-8")
    fh_latest.setLevel(logging.INFO)
    fh_latest.setFormatter(formatter)
    LOGGER.addHandler(fh_latest)


# 3. HTTP Session with automatic retries and headers
def get_http_session() -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    })
    return session


# 4. Build SQLAlchemy Engines
def get_connection_string(database: Optional[str] = None) -> str:
    db = database or DATABASE
    return (
        f"DRIVER={{{DRIVER}}};"
        f"SERVER={SERVER};"
        f"DATABASE={db};"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
    )


def get_engine(database: Optional[str] = None):
    conn_str = get_connection_string(database)
    params = urllib.parse.quote_plus(conn_str)
    return create_engine(f"mssql+pyodbc:///?odbc_connect={params}", fast_executemany=True)


engine = get_engine(DATABASE)


# 5. Database and Staging Tables Verification & Auto-initialization
def ensure_database_and_tables_initialized() -> None:
    """Verifies that the target database and staging tables exist.

    If not found, initializes them using database&table-initialization.sql.
    """
    LOGGER.info("Verifying database '%s' and staging schema existence...", DATABASE)
    
    # 1. Check if database exists
    master_engine = get_engine("master")
    with master_engine.connect() as conn:
        db_exists = conn.execute(
            text("SELECT 1 FROM sys.databases WHERE name = :db"), {"db": DATABASE}
        ).scalar()

    if not db_exists:
        LOGGER.warning("Database '%s' not found. Creating database...", DATABASE)
        with master_engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
            conn.execute(text(f"CREATE DATABASE [{DATABASE}]"))
        LOGGER.info("Database '%s' created successfully.", DATABASE)

    # 2. Check if staging tables exist
    with engine.connect() as conn:
        existing_tables = set(
            row[0]
            for row in conn.execute(
                text("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE='BASE TABLE'")
            ).fetchall()
        )

    missing_tables = STAGE_TABLES - existing_tables
    if missing_tables:
        LOGGER.warning(
            "Missing staging tables: %s. Applying initialization script...",
            missing_tables,
        )
        init_sql_file = SQL_DIR / "database&table-initialization.sql"
        if init_sql_file.exists():
            execute_sql_file(init_sql_file)
            LOGGER.info("Staging schema initialized successfully.")
        else:
            raise FileNotFoundError(f"Schema initialization file not found at: {init_sql_file}")
    else:
        LOGGER.info("Database schema and staging tables verified.")


# 6. Extract & Stage Functions
def stage_wfp_data() -> int:
    LOGGER.info("Fetching Lebanon market prices from WFP / HDX...")
    session = get_http_session()

    response = session.get(WFP_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    df = pd.read_csv(io.BytesIO(response.content), low_memory=False)

    # Drop HXL metadata row if present (#date, #adm1+name, etc.)
    if not df.empty and str(df.iloc[0].get("date", "")).startswith("#"):
        df = df.iloc[1:].reset_index(drop=True)

    df.columns = [str(column).strip().lower() for column in df.columns]
    if "date" not in df.columns:
        raise ValueError("WFP response is missing required column: 'date'")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    numeric_cols = ["latitude", "longitude", "price", "usdprice"]
    for col in numeric_cols:
        if col not in df.columns:
            raise ValueError(f"WFP response is missing required column: '{col}'")
        df[col] = pd.to_numeric(df[col], errors="coerce")

    target_cols = [
        "date", "admin1", "admin2", "market", "latitude", "longitude",
        "category", "commodity", "unit", "priceflag", "pricetype",
        "currency", "price", "usdprice"
    ]
    missing = sorted(set(target_cols) - set(df.columns))
    if missing:
        raise ValueError(f"WFP response is missing columns: {', '.join(missing)}")

    # Clean & stage target columns
    cleaned_df = df[target_cols].copy()
    write_to_stage(cleaned_df, "staging_wfp_prices")

    min_date = cleaned_df["date"].min()
    max_date = cleaned_df["date"].max()
    LOGGER.info(
        "WFP data staged successfully: %d rows (Date Range: %s to %s)",
        len(cleaned_df),
        min_date.strftime("%Y-%m-%d") if pd.notnull(min_date) else "N/A",
        max_date.strftime("%Y-%m-%d") if pd.notnull(max_date) else "N/A",
    )
    return len(cleaned_df)


def stage_worldbank_data() -> int:
    LOGGER.info("Fetching macro indicators from World Bank API...")
    session = get_http_session()

    indicator_codes = [
        code.strip()
        for code in os.getenv("WB_INDICATORS", "FP.CPI.TOTL.ZG,SL.UEM.TOTL.ZS").split(",")
        if code.strip()
    ]
    records = []
    for code in indicator_codes:
        url = f"https://api.worldbank.org/v2/country/LBN/indicator/{code}"
        response = session.get(
            url, params={"format": "json", "per_page": 1000}, timeout=REQUEST_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        if len(data) < 2 or not isinstance(data[1], list):
            raise ValueError(f"Unexpected World Bank response structure for indicator: {code}")
        for item in data[1]:
            if item.get("value") is not None:
                indicator_info = item.get("indicator") or {}
                records.append({
                    "indicator_code": code,
                    "indicator_name": indicator_info.get("value") or code,
                    "year": int(item["date"]),
                    "value": item["value"],
                })

    df_wb = pd.DataFrame(
        records,
        columns=["indicator_code", "indicator_name", "year", "value"],
    )
    df_wb["value"] = pd.to_numeric(df_wb["value"], errors="coerce")
    write_to_stage(df_wb, "staging_world_bank")
    LOGGER.info("World Bank data staged successfully: %d records", len(df_wb))
    return len(df_wb)


def write_to_stage(df: pd.DataFrame, table_name: str) -> None:
    """Safely truncate existing staging data and bulk insert fresh data."""
    if table_name not in STAGE_TABLES:
        raise ValueError(f"Unsupported staging table: {table_name}")
    if df.empty:
        raise ValueError(f"No records received for {table_name}; refusing to clear table")
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE [{table_name}]"))
        df.to_sql(table_name, connection, if_exists="append", index=False)


# 7. SQL Execution & View Refreshes
def execute_sql_file(sql_file_path: pathlib.Path) -> int:
    """Executes a SQL file containing multiple T-SQL batches separated by GO."""
    if not sql_file_path.exists():
        raise FileNotFoundError(f"SQL file not found at: {sql_file_path}")

    sql_text = sql_file_path.read_text(encoding="utf-8")
    # Split by GO lines (case-insensitive)
    raw_batches = re.split(r"^\s*GO\s*$", sql_text, flags=re.IGNORECASE | re.MULTILINE)
    
    executed_count = 0
    with engine.begin() as connection:
        for raw_batch in raw_batches:
            batch = raw_batch.strip()
            if not batch:
                continue
            # Skip standalone USE statements if we are already connected to the database
            if re.match(r"^USE\s+\[?\w+\]?;?$", batch, flags=re.IGNORECASE):
                continue
            connection.execute(text(batch))
            executed_count += 1

    return executed_count


def run_view_definitions(sql_file: str = "views-creation.sql") -> None:
    """Execute SQL file that creates or refreshes the analytical/cleaning views."""
    sql_path = SQL_DIR / sql_file
    LOGGER.info("Refreshing view definitions from %s ...", sql_path.name)
    batches = execute_sql_file(sql_path)
    LOGGER.info("Views created/refreshed successfully (%d batches executed).", batches)


def run_sql_transformations():
    """Run optional SQL stored procedure if explicitly enabled."""
    if os.getenv("RUN_SQL_TRANSFORMATIONS", "false").lower() != "true":
        LOGGER.info("Skipping optional SQL transformations (RUN_SQL_TRANSFORMATIONS is not 'true').")
        return

    LOGGER.info("Checking stored procedure sp_refresh_production_economic_tracker...")
    with engine.connect() as conn:
        proc_exists = conn.execute(
            text(
                "SELECT 1 FROM INFORMATION_SCHEMA.ROUTINES "
                "WHERE ROUTINE_TYPE='PROCEDURE' AND ROUTINE_NAME='sp_refresh_production_economic_tracker'"
            )
        ).scalar()

    if proc_exists:
        LOGGER.info("Executing stored procedure: sp_refresh_production_economic_tracker...")
        with engine.begin() as connection:
            connection.execute(text("EXEC sp_refresh_production_economic_tracker;"))
        LOGGER.info("Production table refreshed successfully.")
    else:
        LOGGER.warning(
            "Stored procedure 'sp_refresh_production_economic_tracker' not found in database. Skipping."
        )


def verify_views_status() -> None:
    """Verifies that the views are accessible and prints row counts."""
    with engine.connect() as conn:
        market_rows = conn.execute(text("SELECT COUNT(*) FROM dbo.vw_lebanon_market_tracker")).scalar()
        macro_rows = conn.execute(text("SELECT COUNT(*) FROM dbo.vw_world_bank_macro")).scalar()
    
    LOGGER.info("--- Data Verification ---")
    LOGGER.info("View [dbo.vw_lebanon_market_tracker] : %d active rows", market_rows)
    LOGGER.info("View [dbo.vw_world_bank_macro]        : %d active rows", macro_rows)


# 8. Main Orchestrator
def main():
    setup_logging()
    start_time = time.time()
    LOGGER.info("=" * 60)
    LOGGER.info("Starting Lebanon Market Tracker ETL & SQL Pipeline")
    LOGGER.info("Target Server: %s | Database: %s", SERVER, DATABASE)
    LOGGER.info("=" * 60)

    try:
        # Step 1: Pre-flight check / schema initialization
        ensure_database_and_tables_initialized()

        # Step 2: Extract and stage WFP food prices
        wfp_count = stage_wfp_data()

        # Step 3: Extract and stage World Bank macro indicators
        wb_count = stage_worldbank_data()

        # Step 4: Create/Update SQL Views
        run_view_definitions()

        # Step 5: Optional transformation SP
        run_sql_transformations()

        # Step 6: Verify views
        verify_views_status()

        elapsed = time.time() - start_time
        LOGGER.info("=" * 60)
        LOGGER.info("SUCCESS: Complete Pipeline finished in %.2f seconds!", elapsed)
        LOGGER.info("Summary: %d WFP prices staged, %d WB records staged.", wfp_count, wb_count)
        LOGGER.info("Log saved to: %s", LOGS_DIR / "pipeline_latest.log")
        LOGGER.info("=" * 60)

    except Exception:
        LOGGER.exception("Pipeline execution FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()