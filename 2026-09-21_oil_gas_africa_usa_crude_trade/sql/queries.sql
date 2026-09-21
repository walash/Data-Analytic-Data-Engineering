-- =====================================================================
-- Analytical SQL queries for the Oil & Gas Africa-USA project
-- Target: SQLite database data/oil_gas.db, table oil_gas_metrics
-- Run with:  sqlite3 data/oil_gas.db < sql/queries.sql
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Top 5 African countries by average oil rents (% of GDP), all years
-- ---------------------------------------------------------------------
SELECT
    iso3,
    country_name,
    ROUND(AVG(oil_rents_pct_gdp), 2) AS avg_oil_rents_pct_gdp,
    COUNT(*) AS years_observed
FROM oil_gas_metrics
WHERE is_african = 1
  AND oil_rents_pct_gdp IS NOT NULL
GROUP BY iso3, country_name
ORDER BY avg_oil_rents_pct_gdp DESC
LIMIT 5;

-- ---------------------------------------------------------------------
-- 2. Year-over-year change in oil rents for Nigeria and Angola
--    using the LAG() window function
-- ---------------------------------------------------------------------
SELECT
    iso3,
    country_name,
    year,
    ROUND(oil_rents_pct_gdp, 2) AS oil_rents_pct_gdp,
    ROUND(
        oil_rents_pct_gdp - LAG(oil_rents_pct_gdp) OVER (
            PARTITION BY iso3 ORDER BY year
        ), 2
    ) AS yoy_change
FROM oil_gas_metrics
WHERE iso3 IN ('NGA', 'AGO')
  AND oil_rents_pct_gdp IS NOT NULL
ORDER BY iso3, year;

-- ---------------------------------------------------------------------
-- 3. Correlation proxy: high oil dependence, low income.
--    Countries where oil rents > 20% of GDP and GDP per capita < 5000 USD
-- ---------------------------------------------------------------------
SELECT
    iso3,
    country_name,
    year,
    ROUND(oil_rents_pct_gdp, 2) AS oil_rents_pct_gdp,
    ROUND(gdp_per_capita_usd, 2) AS gdp_per_capita_usd
FROM oil_gas_metrics
WHERE oil_rents_pct_gdp > 20
  AND gdp_per_capita_usd < 5000
  AND gdp_per_capita_usd IS NOT NULL
ORDER BY oil_rents_pct_gdp DESC;

-- ---------------------------------------------------------------------
-- 4. USA vs Africa average energy use (kg oil equivalent per capita)
--    compared by year
-- ---------------------------------------------------------------------
SELECT
    year,
    ROUND(AVG(CASE WHEN is_african = 1 THEN energy_use_kg_oil_eq END), 1) AS africa_avg_energy_use,
    ROUND(AVG(CASE WHEN is_african = 0 THEN energy_use_kg_oil_eq END), 1) AS usa_energy_use
FROM oil_gas_metrics
WHERE energy_use_kg_oil_eq IS NOT NULL
GROUP BY year
ORDER BY year;

-- ---------------------------------------------------------------------
-- 5. FDI inflows ranking by country (average) using RANK() window function
-- ---------------------------------------------------------------------
SELECT
    iso3,
    country_name,
    ROUND(AVG(fdi_inflows_usd), 0) AS avg_fdi_inflows_usd,
    RANK() OVER (ORDER BY AVG(fdi_inflows_usd) DESC) AS fdi_rank
FROM oil_gas_metrics
WHERE fdi_inflows_usd IS NOT NULL
GROUP BY iso3, country_name
ORDER BY fdi_rank;

-- ---------------------------------------------------------------------
-- 6. Countries with the highest merchandise exports in the latest year
--    available within the dataset
-- ---------------------------------------------------------------------
SELECT
    iso3,
    country_name,
    year,
    ROUND(merchandise_exports_usd, 0) AS merchandise_exports_usd
FROM oil_gas_metrics
WHERE year = (SELECT MAX(year) FROM oil_gas_metrics WHERE merchandise_exports_usd IS NOT NULL)
  AND merchandise_exports_usd IS NOT NULL
ORDER BY merchandise_exports_usd DESC;

-- ---------------------------------------------------------------------
-- 7. Oil dependency ratio trend for the top 3 African oil producers
--    (by average oil rents): Congo, Equatorial Guinea, Angola, etc.
-- ---------------------------------------------------------------------
WITH top3 AS (
    SELECT iso3
    FROM oil_gas_metrics
    WHERE is_african = 1 AND oil_rents_pct_gdp IS NOT NULL
    GROUP BY iso3
    ORDER BY AVG(oil_rents_pct_gdp) DESC
    LIMIT 3
)
SELECT
    m.iso3,
    m.country_name,
    m.year,
    ROUND(m.oil_dependency_ratio, 3) AS oil_dependency_ratio
FROM oil_gas_metrics AS m
JOIN top3 ON m.iso3 = top3.iso3
WHERE m.oil_dependency_ratio IS NOT NULL
ORDER BY m.iso3, m.year;

-- ---------------------------------------------------------------------
-- 8. Countries where electricity from oil sources exceeds 50% of total
-- ---------------------------------------------------------------------
SELECT
    iso3,
    country_name,
    year,
    ROUND(electricity_from_oil_pct, 1) AS electricity_from_oil_pct
FROM oil_gas_metrics
WHERE electricity_from_oil_pct > 50
ORDER BY electricity_from_oil_pct DESC;
