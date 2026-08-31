USE master;
GO

IF NOT EXISTS (SELECT name FROM sys.databases WHERE name = 'lebanon_ecom')
BEGIN 
    CREATE DATABASE lebanon_ecom;
END
GO

USE lebanon_ecom;
GO

-- 1. Staging table for WFP Market Food Prices
IF OBJECT_ID('dbo.staging_wfp_prices', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.staging_wfp_prices (
        date DATETIME2 NULL,
        admin1 NVARCHAR(100) NULL,
        admin2 NVARCHAR(100) NULL,
        market NVARCHAR(150) NULL,
        latitude FLOAT NULL,
        longitude FLOAT NULL,
        category NVARCHAR(100) NULL,
        commodity NVARCHAR(150) NULL,
        unit NVARCHAR(50) NULL,
        priceflag NVARCHAR(50) NULL,
        pricetype NVARCHAR(50) NULL,
        currency NVARCHAR(10) NULL,
        price FLOAT NULL,
        usdprice FLOAT NULL
    );
END
GO

-- 2. Staging table for World Bank Macro Indicators
IF OBJECT_ID('dbo.staging_world_bank', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.staging_world_bank (
        indicator_code NVARCHAR(50) NULL,
        indicator_name NVARCHAR(200) NULL,
        year INT NULL,
        value FLOAT NULL
    );
END
GO