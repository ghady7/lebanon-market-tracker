USE lebanon_ecom;
GO

CREATE OR ALTER VIEW dbo.vw_lebanon_market_tracker AS
SELECT 
    -- 1. Standardize date to month start for monthly rollups
    DATEFROMPARTS(YEAR(date), MONTH(date), 1) AS observation_month,
    CAST(date AS DATE) AS observation_date,

    -- 2. Clean administrative & location names
    admin1 AS governorate,
    admin2 AS district,
    market,
    latitude,
    longitude,

    -- 3. Commodity details
    category,
    commodity,
    unit,
    pricetype,
    currency AS original_currency,

    -- 4. Prices
    round(price,0) AS price_local,
    usdprice AS price_usd
FROM dbo.staging_wfp_prices
WHERE price IS NOT NULL 
  AND price > 0 
  AND usdprice IS NOT NULL;
GO


USE lebanon_ecom;
GO

CREATE OR ALTER VIEW dbo.vw_world_bank_macro AS
SELECT 
    year,
    -- Pivot and round Inflation
    ROUND(MAX(CASE WHEN indicator_code = 'FP.CPI.TOTL.ZG' THEN value END), 2) AS cpi_inflation_annual_pct,
    -- Pivot and round Unemployment
    ROUND(MAX(CASE WHEN indicator_code = 'SL.UEM.TOTL.ZS' THEN value END), 2) AS unemployment_rate_pct
FROM dbo.staging_world_bank
GROUP BY year;
GO