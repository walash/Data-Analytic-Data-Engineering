-- ===========================================================================
-- Africa-USA Skilled Worker Visa Flows - analytical queries
-- Database : data/migration_analytics.db (built by src/loading/load.py)
-- Tables   : countries, migration_indicators, yearly_summary
-- Run      : sqlite3 -header -column data/migration_analytics.db < sql/queries.sql
-- Note     : World Bank net migration is negative when emigrants outnumber
--            immigrants; "net emigration" below means net_migration < 0.
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- Q1. Top countries by net emigration
-- Total net outflow across all years for countries that lost people on net,
-- with how many years they were net senders. These are the largest
-- candidate source pools for skilled-worker visa channels such as H-1B.
-- ---------------------------------------------------------------------------
SELECT
    c.country_name,
    c.subregion,
    SUM(CASE WHEN m.net_migration < 0 THEN -m.net_migration ELSE 0 END) AS total_net_emigration,
    SUM(m.is_net_emigration)                                          AS net_emigration_years,
    SUM(m.net_migration)                                              AS overall_net_migration
FROM migration_indicators m
JOIN countries c ON c.iso3 = m.iso3
GROUP BY c.iso3
HAVING total_net_emigration > 0
ORDER BY total_net_emigration DESC
LIMIT 10;


-- ---------------------------------------------------------------------------
-- Q2. Remittance trends over time
-- Annual personal remittances received (US$ billions) per country, pivoted
-- so each row is a year - diaspora earnings flowing back home.
-- ---------------------------------------------------------------------------
SELECT
    year,
    ROUND(SUM(CASE WHEN iso3 = 'EGY' THEN remittances_usd END) / 1e9, 2) AS egypt_bn,
    ROUND(SUM(CASE WHEN iso3 = 'NGA' THEN remittances_usd END) / 1e9, 2) AS nigeria_bn,
    ROUND(SUM(CASE WHEN iso3 = 'MAR' THEN remittances_usd END) / 1e9, 2) AS morocco_bn,
    ROUND(SUM(CASE WHEN iso3 = 'GHA' THEN remittances_usd END) / 1e9, 2) AS ghana_bn,
    ROUND(SUM(CASE WHEN iso3 = 'KEN' THEN remittances_usd END) / 1e9, 2) AS kenya_bn,
    ROUND(SUM(remittances_usd) / 1e9, 2)                                 AS all_10_countries_bn,
    COUNT(remittances_usd)                                               AS countries_reporting
FROM migration_indicators
GROUP BY year
ORDER BY year;


-- ---------------------------------------------------------------------------
-- Q3. Correlation between GDP per capita and migration
-- Pearson correlation computed in SQL (SQLite has no CORR()) between GDP per
-- capita and the net migration rate per 1,000 residents, per country across
-- years and for the pooled panel.
-- ---------------------------------------------------------------------------
WITH base AS (
    SELECT iso3, gdp_per_capita AS x, net_migration_rate_per_1000 AS y
    FROM migration_indicators
    WHERE gdp_per_capita IS NOT NULL AND net_migration_rate_per_1000 IS NOT NULL
),
stats AS (
    SELECT iso3, COUNT(*) AS n,
           AVG(x) AS mx, AVG(y) AS my,
           AVG(x * x) AS mxx, AVG(y * y) AS myy, AVG(x * y) AS mxy
    FROM base GROUP BY iso3
    UNION ALL
    SELECT 'ALL', COUNT(*), AVG(x), AVG(y), AVG(x * x), AVG(y * y), AVG(x * y)
    FROM base
)
SELECT
    COALESCE(c.country_name, 'Pooled panel')                       AS scope,
    n                                                              AS observations,
    ROUND(mx, 0)                                                   AS avg_gdp_per_capita,
    ROUND(my, 3)                                                   AS avg_net_migration_rate_per_1000,
    ROUND((mxy - mx * my) /
          NULLIF(SQRT((mxx - mx * mx) * (myy - my * my)), 0), 3)   AS pearson_r
FROM stats s
LEFT JOIN countries c ON c.iso3 = s.iso3
ORDER BY (s.iso3 = 'ALL') DESC, pearson_r;


-- ---------------------------------------------------------------------------
-- Q4. Highest tertiary enrollment vs emigration (brain-drain exposure)
-- Countries educating a large share of young adults while also losing
-- people on net are most exposed to skilled-worker brain drain.
-- ---------------------------------------------------------------------------
SELECT
    c.country_name,
    ROUND(AVG(m.tertiary_enrollment), 1)          AS avg_tertiary_enrollment_pct,
    ROUND(AVG(m.emigration_rate_per_1000), 3)     AS avg_emigration_rate_per_1000,
    ROUND(AVG(m.net_migration_rate_per_1000), 3)  AS avg_net_migration_rate_per_1000,
    CASE
        WHEN AVG(m.tertiary_enrollment) >= 20 AND AVG(m.net_migration_rate_per_1000) < 0
            THEN 'High education / net emigration'
        WHEN AVG(m.tertiary_enrollment) >= 20 THEN 'High education / net immigration'
        WHEN AVG(m.net_migration_rate_per_1000) < 0 THEN 'Low education / net emigration'
        ELSE 'Low education / net immigration'
    END                                           AS brain_drain_quadrant,
    ROUND(AVG(m.skilled_worker_proxy_score), 1)   AS avg_skilled_worker_proxy_score
FROM migration_indicators m
JOIN countries c ON c.iso3 = m.iso3
GROUP BY c.iso3
ORDER BY avg_tertiary_enrollment_pct DESC;


-- ---------------------------------------------------------------------------
-- Q5. Year-over-year migration change
-- LAG() window function showing how each country's net migration moved
-- relative to the previous year, flagging switches between sending and
-- receiving status.
-- ---------------------------------------------------------------------------
SELECT
    c.country_name,
    m.year,
    m.net_migration,
    LAG(m.net_migration) OVER w                       AS prev_year_net_migration,
    m.net_migration - LAG(m.net_migration) OVER w     AS yoy_change,
    CASE
        WHEN LAG(m.net_migration) OVER w >= 0 AND m.net_migration < 0 THEN 'Became net sender'
        WHEN LAG(m.net_migration) OVER w < 0 AND m.net_migration >= 0 THEN 'Became net receiver'
        ELSE ''
    END                                               AS status_switch
FROM migration_indicators m
JOIN countries c ON c.iso3 = m.iso3
WINDOW w AS (PARTITION BY m.iso3 ORDER BY m.year)
ORDER BY c.country_name, m.year;


-- ---------------------------------------------------------------------------
-- Q6. Remittances as % of GDP ranking (latest reported year per country)
-- Remittance dependence shows how strongly an economy relies on its diaspora.
-- ---------------------------------------------------------------------------
WITH latest AS (
    SELECT iso3, MAX(year) AS year
    FROM migration_indicators
    WHERE remittances_pct_gdp IS NOT NULL
    GROUP BY iso3
)
SELECT
    RANK() OVER (ORDER BY m.remittances_pct_gdp DESC) AS dependence_rank,
    c.country_name,
    m.year                                            AS latest_year,
    ROUND(m.remittances_pct_gdp, 2)                   AS remittances_pct_gdp,
    ROUND(m.remittances_usd / 1e9, 2)                 AS remittances_usd_bn,
    ROUND(c.avg_remittances_pct_gdp, 2)               AS period_avg_pct_gdp
FROM latest l
JOIN migration_indicators m ON m.iso3 = l.iso3 AND m.year = l.year
JOIN countries c ON c.iso3 = m.iso3
ORDER BY dependence_rank;


-- ---------------------------------------------------------------------------
-- Q7. Population-adjusted migration rates
-- Raw counts favour large countries; rates per 1,000 residents compare
-- Nigeria (230m+) with Senegal (~19m) on equal footing. Last 5 years.
-- ---------------------------------------------------------------------------
SELECT
    c.country_name,
    ROUND(AVG(m.population) / 1e6, 1)              AS avg_population_m,
    ROUND(AVG(m.net_migration), 0)                 AS avg_net_migration,
    ROUND(AVG(m.net_migration_rate_per_1000), 3)   AS avg_rate_per_1000,
    ROUND(MIN(m.net_migration_rate_per_1000), 3)   AS worst_year_rate_per_1000,
    ROUND(AVG(m.migration_intensity_index), 1)     AS avg_migration_intensity_index
FROM migration_indicators m
JOIN countries c ON c.iso3 = m.iso3
WHERE m.year > (SELECT MAX(year) - 5 FROM migration_indicators)
GROUP BY c.iso3
ORDER BY avg_rate_per_1000 ASC;


-- ---------------------------------------------------------------------------
-- Q8. Cumulative remittances by country
-- Running total of remittances (US$ billions) with each country's share of
-- the ten-country total in the same year.
-- ---------------------------------------------------------------------------
SELECT
    c.country_name,
    m.year,
    ROUND(m.remittances_usd / 1e9, 2)                                         AS remittances_bn,
    ROUND(SUM(m.remittances_usd) OVER (PARTITION BY m.iso3 ORDER BY m.year
          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) / 1e9, 2)         AS cumulative_bn,
    ROUND(100.0 * m.remittances_usd / y.total_remittances_usd, 1)             AS share_of_year_total_pct
FROM migration_indicators m
JOIN countries c ON c.iso3 = m.iso3
JOIN yearly_summary y ON y.year = m.year
WHERE m.remittances_usd IS NOT NULL
ORDER BY c.country_name, m.year;
