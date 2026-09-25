-- =====================================================================
-- Analytical SQL queries for the Africa-to-USA Labor Migration database
-- Target: data/labor_migration.db (SQLite)
-- Run with: sqlite3 data/labor_migration.db < sql/queries.sql
-- =====================================================================

-- 1. Top countries by remittances received (latest available year per country).
--    Uses the most recent year each country reports remittances.
WITH latest AS (
    SELECT country_id, MAX(year) AS max_year
    FROM remittances
    WHERE remittances_usd IS NOT NULL
    GROUP BY country_id
)
SELECT c.country_name,
       r.year,
       ROUND(r.remittances_usd / 1e9, 2) AS remittances_billion_usd
FROM remittances r
JOIN latest l ON r.country_id = l.country_id AND r.year = l.max_year
JOIN countries c ON c.country_id = r.country_id
ORDER BY r.remittances_usd DESC;

-- 2. Net migration trend by country over time (negative = net emigration).
SELECT c.country_name,
       n.year,
       n.net_migration
FROM net_migration n
JOIN countries c ON c.country_id = n.country_id
ORDER BY c.country_name, n.year;

-- 3. Remittances as a share of GDP ranking (latest year per country).
WITH latest AS (
    SELECT country_id, MAX(year) AS max_year
    FROM remittances
    WHERE remittances_pct_gdp IS NOT NULL
    GROUP BY country_id
)
SELECT c.country_name,
       r.year,
       ROUND(r.remittances_pct_gdp, 2) AS remittances_pct_gdp
FROM remittances r
JOIN latest l ON r.country_id = l.country_id AND r.year = l.max_year
JOIN countries c ON c.country_id = r.country_id
ORDER BY r.remittances_pct_gdp DESC;

-- 4. Correlation proxy: average unemployment vs average net migration per country.
--    A crude signal of whether weaker labor markets align with net emigration.
SELECT c.country_name,
       ROUND(AVG(m.unemployment), 2)  AS avg_unemployment,
       ROUND(AVG(m.net_migration), 0) AS avg_net_migration
FROM master_analytics m
JOIN countries c ON c.country_id = m.country_id
GROUP BY c.country_name
ORDER BY avg_unemployment DESC;

-- 5. Countries with highest emigration pressure:
--    negative net migration combined with above-median unemployment (latest year).
WITH latest AS (
    SELECT country_id, MAX(year) AS max_year
    FROM master_analytics
    WHERE net_migration IS NOT NULL AND unemployment IS NOT NULL
    GROUP BY country_id
)
SELECT c.country_name,
       m.year,
       m.net_migration,
       ROUND(m.unemployment, 2) AS unemployment
FROM master_analytics m
JOIN latest l ON m.country_id = l.country_id AND m.year = l.max_year
JOIN countries c ON c.country_id = m.country_id
WHERE m.net_migration < 0
ORDER BY m.unemployment DESC, m.net_migration ASC;

-- 6. Year-over-year remittance growth (%) using a self-join on consecutive years.
SELECT c.country_name,
       cur.year,
       ROUND(cur.remittances_usd / 1e9, 2) AS remittances_billion_usd,
       ROUND(
           100.0 * (cur.remittances_usd - prev.remittances_usd)
           / NULLIF(prev.remittances_usd, 0), 2
       ) AS yoy_growth_pct
FROM remittances cur
JOIN remittances prev
     ON cur.country_id = prev.country_id AND cur.year = prev.year + 1
JOIN countries c ON c.country_id = cur.country_id
WHERE cur.remittances_usd IS NOT NULL AND prev.remittances_usd IS NOT NULL
ORDER BY c.country_name, cur.year;

-- 7. GDP per capita vs remittances relationship (latest year per country).
WITH latest AS (
    SELECT country_id, MAX(year) AS max_year
    FROM master_analytics
    WHERE gdp_per_capita IS NOT NULL AND remittances_usd IS NOT NULL
    GROUP BY country_id
)
SELECT c.country_name,
       m.year,
       ROUND(m.gdp_per_capita, 0)          AS gdp_per_capita_usd,
       ROUND(m.remittances_usd / 1e9, 2)   AS remittances_billion_usd,
       ROUND(m.remittance_per_capita, 2)   AS remittance_per_capita_usd
FROM master_analytics m
JOIN latest l ON m.country_id = l.country_id AND m.year = l.max_year
JOIN countries c ON c.country_id = m.country_id
ORDER BY m.gdp_per_capita DESC;

-- 8. Window function: running (cumulative) total of remittances by country over years.
SELECT c.country_name,
       r.year,
       ROUND(r.remittances_usd / 1e9, 2) AS remittances_billion_usd,
       ROUND(
           SUM(r.remittances_usd) OVER (
               PARTITION BY r.country_id ORDER BY r.year
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           ) / 1e9, 2
       ) AS cumulative_remittances_billion_usd
FROM remittances r
JOIN countries c ON c.country_id = r.country_id
WHERE r.remittances_usd IS NOT NULL
ORDER BY c.country_name, r.year;

-- 9. Countries where remittances exceed 5% of GDP (any year on record).
SELECT c.country_name,
       r.year,
       ROUND(r.remittances_pct_gdp, 2) AS remittances_pct_gdp
FROM remittances r
JOIN countries c ON c.country_id = r.country_id
WHERE r.remittances_pct_gdp > 5.0
ORDER BY r.remittances_pct_gdp DESC;

-- 10. Migration balance: net migration per 1,000 population (latest year per country).
WITH latest AS (
    SELECT country_id, MAX(year) AS max_year
    FROM master_analytics
    WHERE migration_rate_per_1000 IS NOT NULL
    GROUP BY country_id
)
SELECT c.country_name,
       m.year,
       ROUND(m.migration_rate_per_1000, 3) AS net_migration_per_1000
FROM master_analytics m
JOIN latest l ON m.country_id = l.country_id AND m.year = l.max_year
JOIN countries c ON c.country_id = m.country_id
ORDER BY m.migration_rate_per_1000 ASC;
