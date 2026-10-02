-- ===========================================================================
-- Africa LNG Exports to the USA - analytical queries
-- Target: SQLite database data/lng_pipeline.db (built by src/loading/load.py)
-- Run:    sqlite3 -header -column data/lng_pipeline.db < sql/queries.sql
--
-- Tables
--   lng_trade_raw          row-level UN Comtrade HS 271111 trade (all flows,
--                          partner_code 0 = "World" aggregate, 842 = USA)
--   wb_indicators          World Bank indicators, long format
--   wb_indicators_wide     World Bank indicators, one row per country-year
--   lng_exports_processed  exporter-year analytical table
-- Note: the bundled Comtrade snapshot covers 2019, 2020 and 2023 for these
-- reporters, and World Bank rents series currently end in 2021.
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- 1. Top African LNG exporters by total trade value (2019-2023)
--    Uses the exporter-year table, which takes the Comtrade "World" total so
--    bilateral rows are not double counted.
-- ---------------------------------------------------------------------------
SELECT
    country,
    COUNT(*)                                   AS years_reported,
    ROUND(SUM(export_value_billion_usd), 3)    AS total_exports_usd_bn,
    ROUND(AVG(export_value_billion_usd), 3)    AS avg_annual_exports_usd_bn,
    ROUND(MAX(export_value_billion_usd), 3)    AS peak_year_exports_usd_bn
FROM lng_exports_processed
WHERE year BETWEEN 2019 AND 2023
GROUP BY country
ORDER BY total_exports_usd_bn DESC;


-- ---------------------------------------------------------------------------
-- 2. Year-over-year LNG export growth by country
--    LAG() compares each year with the previous *available* year. Growth is
--    suppressed when the base is below USD 10m to avoid meaningless ratios.
-- ---------------------------------------------------------------------------
SELECT
    country,
    year,
    LAG(year) OVER w                                       AS prev_year,
    ROUND(export_value_billion_usd, 3)                     AS exports_usd_bn,
    ROUND(LAG(export_value_billion_usd) OVER w, 3)         AS prev_exports_usd_bn,
    CASE
        WHEN LAG(total_export_value_usd) OVER w >= 1e7
        THEN ROUND(100.0 * (total_export_value_usd - LAG(total_export_value_usd) OVER w)
                   / LAG(total_export_value_usd) OVER w, 1)
    END                                                    AS growth_pct
FROM lng_exports_processed
WINDOW w AS (PARTITION BY country ORDER BY year)
ORDER BY country, year;


-- ---------------------------------------------------------------------------
-- 3. African LNG exports to the USA specifically (partner_code 842)
-- ---------------------------------------------------------------------------
SELECT
    t.reporter                                        AS exporter,
    t.year,
    ROUND(t.trade_value_usd / 1e6, 3)                 AS exports_to_usa_usd_m,
    ROUND(100.0 * t.trade_value_usd / p.total_export_value_usd, 2)
                                                      AS share_of_country_exports_pct
FROM lng_trade_raw AS t
JOIN lng_exports_processed AS p
  ON p.reporter_code = t.reporter_code AND p.year = t.year
WHERE t.flow = 'X'
  AND t.partner_code = 842
ORDER BY t.year, exports_to_usa_usd_m DESC;


-- ---------------------------------------------------------------------------
-- 4. Countries most dependent on gas rents (% of GDP)
--    Average 2019-2021 (latest World Bank coverage) plus the latest value.
-- ---------------------------------------------------------------------------
SELECT
    country,
    ROUND(AVG(gas_rents_pct_gdp), 2)                            AS avg_gas_rents_pct_gdp_2019_21,
    ROUND(MAX(CASE WHEN year = 2021 THEN gas_rents_pct_gdp END), 2) AS gas_rents_pct_gdp_2021,
    ROUND(AVG(total_resource_rents_pct_gdp), 2)                 AS avg_total_rents_pct_gdp,
    ROUND(100.0 * AVG(gas_rents_pct_gdp)
          / NULLIF(AVG(total_resource_rents_pct_gdp), 0), 1)    AS gas_share_of_resource_rents_pct
FROM wb_indicators_wide
WHERE year BETWEEN 2019 AND 2021
GROUP BY country
ORDER BY avg_gas_rents_pct_gdp_2019_21 DESC;


-- ---------------------------------------------------------------------------
-- 5. Correlation between gas rents and merchandise exports
--    Pearson r computed from aggregates (SQLite has no CORR function), both
--    pooled across countries and within each country over time (2012-2021).
-- ---------------------------------------------------------------------------
WITH pairs AS (
    SELECT country, gas_rents_pct_gdp AS x, merchandise_exports_usd / 1e9 AS y
    FROM wb_indicators_wide
    WHERE gas_rents_pct_gdp IS NOT NULL AND merchandise_exports_usd IS NOT NULL
),
stats AS (
    SELECT 'ALL COUNTRIES (pooled)' AS scope, COUNT(*) AS n,
           SUM(x) AS sx, SUM(y) AS sy, SUM(x * x) AS sxx,
           SUM(y * y) AS syy, SUM(x * y) AS sxy
    FROM pairs
    UNION ALL
    SELECT country, COUNT(*), SUM(x), SUM(y), SUM(x * x), SUM(y * y), SUM(x * y)
    FROM pairs
    GROUP BY country
)
SELECT
    scope,
    n AS observations,
    ROUND((n * sxy - sx * sy)
          / NULLIF(SQRT((n * sxx - sx * sx) * (n * syy - sy * sy)), 0), 3)
        AS pearson_r_gas_rents_vs_merch_exports
FROM stats
WHERE n >= 3
ORDER BY scope = 'ALL COUNTRIES (pooled)' DESC, pearson_r_gas_rents_vs_merch_exports DESC;


-- ---------------------------------------------------------------------------
-- 6. LNG export market share by country per year (share of the African
--    reporters' combined exports in that year)
-- ---------------------------------------------------------------------------
SELECT
    year,
    country,
    ROUND(export_value_billion_usd, 3)                                    AS exports_usd_bn,
    ROUND(100.0 * total_export_value_usd
          / SUM(total_export_value_usd) OVER (PARTITION BY year), 2)      AS market_share_pct,
    RANK() OVER (PARTITION BY year ORDER BY total_export_value_usd DESC)  AS rank_in_year
FROM lng_exports_processed
ORDER BY year, rank_in_year;


-- ---------------------------------------------------------------------------
-- 7. Countries with the highest share of electricity generated from gas
--    Latest available year per country from the long indicator table.
-- ---------------------------------------------------------------------------
WITH latest AS (
    SELECT country, year, value,
           ROW_NUMBER() OVER (PARTITION BY country_code ORDER BY year DESC) AS rn
    FROM wb_indicators
    WHERE indicator_name = 'electricity_from_gas_pct'
)
SELECT country, year AS latest_year, ROUND(value, 1) AS electricity_from_gas_pct
FROM latest
WHERE rn = 1
ORDER BY electricity_from_gas_pct DESC;


-- ---------------------------------------------------------------------------
-- 8. GDP per capita vs gas rents ranking (2021, latest common year)
--    A large gap between the two ranks highlights gas-rich but income-poor
--    economies (or the reverse).
-- ---------------------------------------------------------------------------
SELECT
    country,
    ROUND(gdp_per_capita_usd, 0)                                   AS gdp_per_capita_usd,
    ROUND(gas_rents_pct_gdp, 2)                                    AS gas_rents_pct_gdp,
    RANK() OVER (ORDER BY gdp_per_capita_usd DESC)                 AS gdp_per_capita_rank,
    RANK() OVER (ORDER BY gas_rents_pct_gdp DESC)                  AS gas_rents_rank,
    RANK() OVER (ORDER BY gas_rents_pct_gdp DESC)
      - RANK() OVER (ORDER BY gdp_per_capita_usd DESC)             AS rank_gap
FROM wb_indicators_wide
WHERE year = 2021
ORDER BY gas_rents_rank;


-- ---------------------------------------------------------------------------
-- 9. Window function: running total of LNG exports by country
-- ---------------------------------------------------------------------------
SELECT
    country,
    year,
    ROUND(export_value_billion_usd, 3)                               AS exports_usd_bn,
    ROUND(SUM(export_value_billion_usd) OVER (
              PARTITION BY country ORDER BY year
              ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 3)  AS running_total_usd_bn,
    ROUND(SUM(usa_export_value_million_usd) OVER (
              PARTITION BY country ORDER BY year
              ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 3)  AS running_total_to_usa_usd_m
FROM lng_exports_processed
ORDER BY country, year;


-- ---------------------------------------------------------------------------
-- 10. Countries where the USA is the top export destination
--     Ranks every bilateral export partner per exporter-year, returns every
--     exporter-year that shipped LNG to the USA with the USA's rank, and
--     flags the cases where the USA is the #1 destination.
-- ---------------------------------------------------------------------------
WITH ranked AS (
    SELECT
        reporter, year, partner_code, partner, trade_value_usd,
        RANK() OVER (PARTITION BY reporter, year
                     ORDER BY trade_value_usd DESC)          AS dest_rank,
        COUNT(*) OVER (PARTITION BY reporter, year)          AS n_destinations,
        FIRST_VALUE(partner) OVER (PARTITION BY reporter, year
                                   ORDER BY trade_value_usd DESC) AS top_destination
    FROM lng_trade_raw
    WHERE flow = 'X' AND partner_code <> 0 AND trade_value_usd > 0
)
SELECT
    reporter                         AS exporter,
    year,
    ROUND(trade_value_usd / 1e6, 4)  AS exports_to_usa_usd_m,
    dest_rank                        AS usa_destination_rank,
    n_destinations,
    top_destination,
    CASE WHEN dest_rank = 1 THEN 'YES' ELSE 'no' END AS usa_is_top_destination
FROM ranked
WHERE partner_code = 842
ORDER BY usa_is_top_destination DESC, year, usa_destination_rank;
