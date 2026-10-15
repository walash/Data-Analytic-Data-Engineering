-- =============================================================================
-- Analytical SQL queries for the Africa-USA Maritime Port Trade project.
--
-- Target database : data/maritime_ports.db (SQLite)
-- Main table      : maritime_indicators (country, iso3, year, liner_shipping_index,
--                   container_throughput_teu, merchandise_trade_pct_gdp, lpi_score,
--                   exports_usd, region, sub_region, shipping_trade_ratio,
--                   port_efficiency_score)
-- Support tables  : country_metadata, yearly_summary
--
-- Run with: sqlite3 data/maritime_ports.db < sql/queries.sql
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Query 1: Top 10 countries by average liner shipping connectivity index.
-- Higher values indicate a country is better connected to global liner shipping
-- networks (South Africa, Egypt and Morocco typically lead in Africa).
-- -----------------------------------------------------------------------------
SELECT
    country,
    iso3,
    ROUND(AVG(liner_shipping_index), 2) AS avg_connectivity,
    COUNT(*)                            AS years_observed
FROM maritime_indicators
WHERE liner_shipping_index IS NOT NULL
GROUP BY country, iso3
ORDER BY avg_connectivity DESC
LIMIT 10;


-- -----------------------------------------------------------------------------
-- Query 2: Year-over-year growth in container port throughput for the top 5
-- African ports (by most recent throughput). Uses a self-comparison via LAG.
-- -----------------------------------------------------------------------------
WITH african_throughput AS (
    SELECT
        country,
        iso3,
        year,
        container_throughput_teu,
        LAG(container_throughput_teu) OVER (
            PARTITION BY iso3 ORDER BY year
        ) AS prev_teu
    FROM maritime_indicators
    WHERE region = 'Africa'
      AND container_throughput_teu IS NOT NULL
),
top5 AS (
    SELECT iso3
    FROM maritime_indicators
    WHERE region = 'Africa' AND container_throughput_teu IS NOT NULL
    GROUP BY iso3
    ORDER BY MAX(year) DESC, MAX(container_throughput_teu) DESC
    LIMIT 5
)
SELECT
    a.country,
    a.year,
    a.container_throughput_teu,
    a.prev_teu,
    ROUND(
        100.0 * (a.container_throughput_teu - a.prev_teu) / a.prev_teu, 2
    ) AS yoy_growth_pct
FROM african_throughput a
JOIN top5 t ON a.iso3 = t.iso3
WHERE a.prev_teu IS NOT NULL
ORDER BY a.country, a.year;


-- -----------------------------------------------------------------------------
-- Query 3: Correlation proxy - countries that combine high logistics
-- performance (LPI) with high shipping connectivity. Uses each country's most
-- recent non-null value for both metrics.
-- -----------------------------------------------------------------------------
WITH latest_lpi AS (
    SELECT iso3, lpi_score,
           ROW_NUMBER() OVER (PARTITION BY iso3 ORDER BY year DESC) AS rn
    FROM maritime_indicators
    WHERE lpi_score IS NOT NULL
),
latest_conn AS (
    SELECT iso3, liner_shipping_index,
           ROW_NUMBER() OVER (PARTITION BY iso3 ORDER BY year DESC) AS rn
    FROM maritime_indicators
    WHERE liner_shipping_index IS NOT NULL
)
SELECT
    m.country,
    m.iso3,
    ROUND(l.lpi_score, 2)            AS latest_lpi,
    ROUND(c.liner_shipping_index, 2) AS latest_connectivity
FROM country_metadata m
JOIN latest_lpi  l ON m.iso3 = l.iso3  AND l.rn = 1
JOIN latest_conn c ON m.iso3 = c.iso3  AND c.rn = 1
WHERE l.lpi_score >= 3.0
  AND c.liner_shipping_index >= 40
ORDER BY latest_lpi DESC, latest_connectivity DESC;


-- -----------------------------------------------------------------------------
-- Query 4: Africa vs USA comparison of average merchandise trade (% of GDP)
-- by year.
-- -----------------------------------------------------------------------------
SELECT
    year,
    region,
    ROUND(AVG(merchandise_trade_pct_gdp), 2) AS avg_merch_trade_pct_gdp,
    COUNT(DISTINCT iso3)                      AS country_count
FROM maritime_indicators
WHERE merchandise_trade_pct_gdp IS NOT NULL
  AND region IN ('Africa', 'USA')
GROUP BY year, region
ORDER BY year, region;


-- -----------------------------------------------------------------------------
-- Query 5: Countries with the fastest-growing container throughput, comparing
-- earliest vs latest observed year (window functions FIRST_VALUE/LAST_VALUE).
-- -----------------------------------------------------------------------------
WITH bounds AS (
    SELECT
        country,
        iso3,
        FIRST_VALUE(container_throughput_teu) OVER (
            PARTITION BY iso3 ORDER BY year
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        ) AS first_teu,
        LAST_VALUE(container_throughput_teu) OVER (
            PARTITION BY iso3 ORDER BY year
            ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
        ) AS last_teu,
        MIN(year) OVER (PARTITION BY iso3) AS first_year,
        MAX(year) OVER (PARTITION BY iso3) AS last_year
    FROM maritime_indicators
    WHERE container_throughput_teu IS NOT NULL
)
SELECT DISTINCT
    country,
    iso3,
    first_year,
    last_year,
    first_teu,
    last_teu,
    ROUND(100.0 * (last_teu - first_teu) / first_teu, 2) AS total_growth_pct
FROM bounds
WHERE first_teu > 0 AND last_year > first_year
ORDER BY total_growth_pct DESC;


-- -----------------------------------------------------------------------------
-- Query 6: LPI score ranking with percentile using a window function.
-- Uses each country's latest LPI observation.
-- -----------------------------------------------------------------------------
WITH latest_lpi AS (
    SELECT
        country,
        iso3,
        lpi_score,
        year,
        ROW_NUMBER() OVER (PARTITION BY iso3 ORDER BY year DESC) AS rn
    FROM maritime_indicators
    WHERE lpi_score IS NOT NULL
)
SELECT
    country,
    iso3,
    year          AS lpi_year,
    ROUND(lpi_score, 2) AS lpi_score,
    RANK()       OVER (ORDER BY lpi_score DESC)          AS lpi_rank,
    ROUND(PERCENT_RANK() OVER (ORDER BY lpi_score), 3)   AS lpi_percentile
FROM latest_lpi
WHERE rn = 1
ORDER BY lpi_rank;


-- -----------------------------------------------------------------------------
-- Query 7: Countries where exports GREW while shipping connectivity DECLINED
-- (a divergence signal), comparing earliest and latest observations of each.
-- -----------------------------------------------------------------------------
WITH exp_bounds AS (
    SELECT iso3,
           FIRST_VALUE(exports_usd) OVER (
               PARTITION BY iso3 ORDER BY year
               ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
           ) AS first_exp,
           LAST_VALUE(exports_usd) OVER (
               PARTITION BY iso3 ORDER BY year
               ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
           ) AS last_exp
    FROM maritime_indicators
    WHERE exports_usd IS NOT NULL
),
conn_bounds AS (
    SELECT iso3,
           FIRST_VALUE(liner_shipping_index) OVER (
               PARTITION BY iso3 ORDER BY year
               ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
           ) AS first_conn,
           LAST_VALUE(liner_shipping_index) OVER (
               PARTITION BY iso3 ORDER BY year
               ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
           ) AS last_conn
    FROM maritime_indicators
    WHERE liner_shipping_index IS NOT NULL
)
SELECT DISTINCT
    m.country,
    m.iso3,
    ROUND(100.0 * (e.last_exp - e.first_exp) / e.first_exp, 2)   AS exports_growth_pct,
    ROUND(c.last_conn - c.first_conn, 2)                          AS connectivity_change
FROM country_metadata m
JOIN exp_bounds  e ON m.iso3 = e.iso3
JOIN conn_bounds c ON m.iso3 = c.iso3
WHERE e.first_exp > 0
  AND e.last_exp > e.first_exp        -- exports grew
  AND c.last_conn < c.first_conn      -- connectivity declined
ORDER BY exports_growth_pct DESC;


-- -----------------------------------------------------------------------------
-- Query 8: Regional summary - total container throughput by African
-- sub-region (latest year available per country).
-- -----------------------------------------------------------------------------
WITH latest_teu AS (
    SELECT
        iso3,
        sub_region,
        container_throughput_teu,
        ROW_NUMBER() OVER (PARTITION BY iso3 ORDER BY year DESC) AS rn
    FROM maritime_indicators
    WHERE container_throughput_teu IS NOT NULL
      AND region = 'Africa'
)
SELECT
    sub_region,
    COUNT(DISTINCT iso3)                 AS country_count,
    SUM(container_throughput_teu)        AS total_container_teu,
    ROUND(AVG(container_throughput_teu), 0) AS avg_container_teu
FROM latest_teu
WHERE rn = 1
GROUP BY sub_region
ORDER BY total_container_teu DESC;
