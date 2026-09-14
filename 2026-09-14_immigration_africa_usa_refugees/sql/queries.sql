-- =============================================================================
-- Analytical SQL queries for the Africa-to-USA Immigration & Refugee project.
--
-- Target database : data/immigration_analytics.db  (SQLite)
-- Populated by     : src/loading/load.py
--
-- Run interactively with:
--     sqlite3 data/immigration_analytics.db < sql/queries.sql
-- or execute individual queries from the Jupyter notebook.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 1. Top 10 African countries by net EMIGRATION (most negative net migration).
--    We aggregate net migration across all available years so a persistently
--    emigrating country ranks above one with a single bad year.
-- -----------------------------------------------------------------------------
SELECT
    country,
    iso3,
    region,
    ROUND(SUM(net_migration), 0)              AS total_net_migration,
    ROUND(AVG(net_migration), 0)              AS avg_net_migration,
    COUNT(*)                                  AS years_observed
FROM african_migration
WHERE net_migration IS NOT NULL
GROUP BY country, iso3, region
ORDER BY total_net_migration ASC          -- most negative first
LIMIT 10;


-- -----------------------------------------------------------------------------
-- 2. Countries with the highest REMITTANCE DEPENDENCY (avg % of GDP).
--    Remittances are a key economic counterpart to emigration.
-- -----------------------------------------------------------------------------
SELECT
    country,
    iso3,
    ROUND(AVG(remittances_pct_gdp), 2)        AS avg_remittances_pct_gdp,
    ROUND(MAX(remittances_pct_gdp), 2)        AS peak_remittances_pct_gdp,
    COUNT(*)                                  AS years_observed
FROM remittances
WHERE remittances_pct_gdp IS NOT NULL
GROUP BY country, iso3
ORDER BY avg_remittances_pct_gdp DESC
LIMIT 10;


-- -----------------------------------------------------------------------------
-- 3. Year-over-year migration trend for the TOP 5 emigrating countries.
--    Uses a CTE to identify the top emigrators, then returns their full
--    yearly time series for the last 15 years.
-- -----------------------------------------------------------------------------
WITH top_emigrators AS (
    SELECT iso3
    FROM african_migration
    WHERE net_migration IS NOT NULL
    GROUP BY iso3
    ORDER BY SUM(net_migration) ASC
    LIMIT 5
)
SELECT
    m.country,
    m.iso3,
    m.year,
    m.net_migration,
    ROUND(m.migration_rate_per_1000, 2)       AS migration_rate_per_1000
FROM african_migration AS m
JOIN top_emigrators AS t ON m.iso3 = t.iso3
WHERE m.year >= 2010
ORDER BY m.iso3, m.year;


-- -----------------------------------------------------------------------------
-- 4. Relationship between REMITTANCES and NET MIGRATION per country.
--    Aligns the two indicators on (iso3, year) and produces the per-country
--    averages needed to inspect a possible correlation.
-- -----------------------------------------------------------------------------
SELECT
    m.country,
    m.iso3,
    ROUND(AVG(m.net_migration), 0)            AS avg_net_migration,
    ROUND(AVG(r.remittances_pct_gdp), 2)      AS avg_remittances_pct_gdp,
    COUNT(*)                                  AS matched_years
FROM african_migration AS m
JOIN remittances AS r
    ON m.iso3 = r.iso3 AND m.year = r.year
WHERE m.net_migration IS NOT NULL
  AND r.remittances_pct_gdp IS NOT NULL
GROUP BY m.country, m.iso3
HAVING matched_years >= 5
ORDER BY avg_remittances_pct_gdp DESC;


-- -----------------------------------------------------------------------------
-- 5. USA ASYLUM DECISIONS trend 2010-2023.
--    Yearly totals with the recognition rate (approvals / total decided).
-- -----------------------------------------------------------------------------
SELECT
    year,
    SUM(recognized)                           AS recognized,
    SUM(rejected)                             AS rejected,
    SUM(other)                                AS other,
    SUM(closed)                               AS closed,
    SUM(total)                                AS total_decisions,
    ROUND(100.0 * SUM(recognized) /
          NULLIF(SUM(recognized) + SUM(rejected), 0), 1) AS recognition_rate_pct
FROM asylum_decisions
GROUP BY year
ORDER BY year;


-- -----------------------------------------------------------------------------
-- 6. Countries with IMPROVING vs WORSENING migration balance.
--    Compares each country's average net migration in the most recent 5 years
--    against the earliest 5 years for which we have data.
-- -----------------------------------------------------------------------------
WITH ranked AS (
    SELECT
        iso3, country, year, net_migration,
        ROW_NUMBER() OVER (PARTITION BY iso3 ORDER BY year ASC)  AS asc_rank,
        ROW_NUMBER() OVER (PARTITION BY iso3 ORDER BY year DESC) AS desc_rank
    FROM african_migration
    WHERE net_migration IS NOT NULL
),
early AS (
    SELECT iso3, country, AVG(net_migration) AS early_avg
    FROM ranked WHERE asc_rank <= 5 GROUP BY iso3, country
),
recent AS (
    SELECT iso3, AVG(net_migration) AS recent_avg
    FROM ranked WHERE desc_rank <= 5 GROUP BY iso3
)
SELECT
    e.country,
    e.iso3,
    ROUND(e.early_avg, 0)                      AS early_avg_net_migration,
    ROUND(r.recent_avg, 0)                     AS recent_avg_net_migration,
    ROUND(r.recent_avg - e.early_avg, 0)       AS change,
    CASE WHEN r.recent_avg > e.early_avg THEN 'Improving'
         ELSE 'Worsening' END                  AS trend
FROM early AS e
JOIN recent AS r ON e.iso3 = r.iso3
ORDER BY change DESC;


-- -----------------------------------------------------------------------------
-- 7. REGIONAL analysis: North Africa vs Sub-Saharan Africa.
--    Aggregates the key indicators by region for the latest 10 years.
-- -----------------------------------------------------------------------------
SELECT
    region,
    COUNT(DISTINCT iso3)                       AS countries,
    ROUND(SUM(net_migration), 0)              AS total_net_migration,
    ROUND(AVG(net_migration), 0)              AS avg_net_migration,
    ROUND(AVG(migration_rate_per_1000), 3)    AS avg_migration_rate_per_1000
FROM african_migration
WHERE net_migration IS NOT NULL
  AND year >= 2015
GROUP BY region
ORDER BY total_net_migration ASC;


-- -----------------------------------------------------------------------------
-- 8. POPULATION-ADJUSTED migration rates (latest year per country).
--    Net migration per 1,000 inhabitants normalises for country size, so
--    small high-outflow states surface alongside large ones.
-- -----------------------------------------------------------------------------
WITH latest AS (
    SELECT iso3, MAX(year) AS max_year
    FROM african_migration
    WHERE net_migration IS NOT NULL AND population IS NOT NULL
    GROUP BY iso3
)
SELECT
    m.country,
    m.iso3,
    m.region,
    m.year,
    m.net_migration,
    ROUND(m.population, 0)                     AS population,
    ROUND(m.migration_rate_per_1000, 3)       AS migration_rate_per_1000
FROM african_migration AS m
JOIN latest AS l ON m.iso3 = l.iso3 AND m.year = l.max_year
ORDER BY m.migration_rate_per_1000 ASC        -- strongest per-capita outflow first
LIMIT 15;
