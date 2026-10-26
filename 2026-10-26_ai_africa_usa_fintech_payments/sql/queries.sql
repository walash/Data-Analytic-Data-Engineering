-- ===========================================================================
-- AI-Driven Fintech & Digital Payments: Africa and USA
-- Analytical queries against data/fintech_africa_usa.db (SQLite >= 3.25)
--
-- Run all:  sqlite3 -header -column data/fintech_africa_usa.db < sql/queries.sql
-- Tables:   raw_indicators, fintech_master, country_summary
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- 1. Top 5 African countries by financial inclusion rate (latest Findex year)
--    Account ownership (% of adults 15+) in the most recent survey round that
--    has data, restricted to African countries.
-- ---------------------------------------------------------------------------
WITH latest_year AS (
    SELECT MAX(year) AS year
    FROM fintech_master
    WHERE region = 'Africa' AND financial_inclusion_pct IS NOT NULL
)
SELECT
    m.country_name,
    m.sub_region,
    m.year,
    ROUND(m.financial_inclusion_pct, 1) AS financial_inclusion_pct
FROM fintech_master AS m
JOIN latest_year AS ly ON m.year = ly.year
WHERE m.region = 'Africa'
  AND m.financial_inclusion_pct IS NOT NULL
ORDER BY m.financial_inclusion_pct DESC
LIMIT 5;


-- ---------------------------------------------------------------------------
-- 2. Year-over-year growth in internet users by country
--    Percentage-point change and relative growth versus the previous year,
--    computed with LAG() over each country's time series.
-- ---------------------------------------------------------------------------
SELECT
    country_name,
    year,
    ROUND(internet_users_pct, 2)                                   AS internet_users_pct,
    ROUND(internet_users_pct - LAG(internet_users_pct) OVER w, 2)  AS yoy_change_pp,
    ROUND(100.0 * (internet_users_pct - LAG(internet_users_pct) OVER w)
          / NULLIF(LAG(internet_users_pct) OVER w, 0), 2)          AS yoy_growth_pct
FROM fintech_master
WHERE internet_users_pct IS NOT NULL
WINDOW w AS (PARTITION BY country_code ORDER BY year)
ORDER BY country_name, year;


-- ---------------------------------------------------------------------------
-- 3. Correlation proxy: mobile penetration vs financial inclusion
--    Buckets countries by whether they sit above/below the median mobile
--    subscription rate and the median account-ownership rate (latest values).
--    "High mobile / low inclusion" countries are the biggest mobile-money
--    opportunity, while "high / high" countries show mobile driving inclusion.
-- ---------------------------------------------------------------------------
WITH ranked AS (
    SELECT
        country_name,
        region,
        mobile_subs_per_100,
        financial_inclusion_pct,
        PERCENT_RANK() OVER (ORDER BY mobile_subs_per_100)     AS mobile_prank,
        PERCENT_RANK() OVER (ORDER BY financial_inclusion_pct) AS inclusion_prank
    FROM country_summary
    WHERE mobile_subs_per_100 IS NOT NULL AND financial_inclusion_pct IS NOT NULL
)
SELECT
    country_name,
    region,
    ROUND(mobile_subs_per_100, 1)     AS mobile_subs_per_100,
    ROUND(financial_inclusion_pct, 1) AS financial_inclusion_pct,
    CASE
        WHEN mobile_prank >= 0.5 AND inclusion_prank >= 0.5 THEN 'High mobile / High inclusion'
        WHEN mobile_prank >= 0.5 AND inclusion_prank <  0.5 THEN 'High mobile / Low inclusion (opportunity)'
        WHEN mobile_prank <  0.5 AND inclusion_prank >= 0.5 THEN 'Low mobile / High inclusion'
        ELSE 'Low mobile / Low inclusion'
    END AS segment
FROM ranked
ORDER BY segment, mobile_subs_per_100 DESC;


-- ---------------------------------------------------------------------------
-- 4. Remittance inflows ranked by country (latest available year per country)
-- ---------------------------------------------------------------------------
SELECT
    RANK() OVER (ORDER BY remittances_usd DESC)  AS remittance_rank,
    country_name,
    region,
    remittances_usd_year                         AS year,
    ROUND(remittances_usd / 1e9, 2)              AS remittances_usd_bn
FROM country_summary
WHERE remittances_usd IS NOT NULL
ORDER BY remittance_rank;


-- ---------------------------------------------------------------------------
-- 5. Countries where remittances exceed 5% of GDP (latest year per country)
--    remittance_to_gdp_ratio is stored as a percentage:
--    remittances / (GDP per capita x population) x 100.
-- ---------------------------------------------------------------------------
SELECT
    country_name,
    remittance_to_gdp_ratio_year       AS year,
    ROUND(remittances_usd / 1e9, 2)    AS remittances_usd_bn,
    ROUND(remittance_to_gdp_ratio, 2)  AS remittance_pct_of_gdp
FROM country_summary
WHERE remittance_to_gdp_ratio > 5
ORDER BY remittance_to_gdp_ratio DESC;


-- ---------------------------------------------------------------------------
-- 6. Fintech readiness score ranking
--    Composite (0-100) = 0.4 x account ownership + 0.3 x internet use
--                      + 0.3 x mobile subscriptions (capped at 100 per 100).
--    Also shows the gap of each country to the USA benchmark.
-- ---------------------------------------------------------------------------
SELECT
    readiness_rank,
    country_name,
    region,
    fintech_readiness_score_year                     AS year,
    ROUND(fintech_readiness_score, 1)                AS fintech_readiness_score,
    ROUND(fintech_readiness_score - (
        SELECT fintech_readiness_score FROM country_summary WHERE country_code = 'USA'
    ), 1)                                            AS gap_to_usa
FROM country_summary
WHERE fintech_readiness_score IS NOT NULL
ORDER BY readiness_rank;


-- ---------------------------------------------------------------------------
-- 7. Window function: running average of financial inclusion by country
--    Cumulative mean of account ownership across every Findex round
--    (2014, 2017, 2021, 2022, 2024) available for each country.
-- ---------------------------------------------------------------------------
SELECT
    country_name,
    year,
    ROUND(financial_inclusion_pct, 2) AS financial_inclusion_pct,
    ROUND(AVG(financial_inclusion_pct) OVER (
        PARTITION BY country_code
        ORDER BY year
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ), 2)                             AS running_avg_inclusion_pct,
    COUNT(*) OVER (
        PARTITION BY country_code
        ORDER BY year
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    )                                 AS survey_rounds_so_far
FROM fintech_master
WHERE financial_inclusion_pct IS NOT NULL
ORDER BY country_name, year;


-- ---------------------------------------------------------------------------
-- 8. Fastest improvement in financial inclusion (2017 vs 2024)
--    Percentage-point change between the 2017 and 2024 Findex rounds - only
--    countries surveyed in both rounds are included.
-- ---------------------------------------------------------------------------
SELECT
    f17.country_name,
    f17.region,
    ROUND(f17.financial_inclusion_pct, 1)                              AS inclusion_2017,
    ROUND(f24.financial_inclusion_pct, 1)                              AS inclusion_2024,
    ROUND(f24.financial_inclusion_pct - f17.financial_inclusion_pct, 1) AS change_pp,
    ROUND(100.0 * (f24.financial_inclusion_pct - f17.financial_inclusion_pct)
          / f17.financial_inclusion_pct, 1)                            AS relative_change_pct
FROM fintech_master AS f17
JOIN fintech_master AS f24
  ON f24.country_code = f17.country_code AND f24.year = 2024
WHERE f17.year = 2017
  AND f17.financial_inclusion_pct IS NOT NULL
  AND f24.financial_inclusion_pct IS NOT NULL
ORDER BY change_pp DESC;
