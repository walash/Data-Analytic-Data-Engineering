-- =============================================================================
-- Africa-USA Road Infrastructure & Cross-Border Trade Corridors
-- Analytical queries against data/transport_trade.db (SQLite 3.25+)
--
-- Run:  sqlite3 -header -column data/transport_trade.db < sql/queries.sql
--
-- Tables (created by src/loading/load.py):
--   raw_indicators          long format, observed values only
--   master_transport_trade  wide country-year table (forward-filled)
--   country_summary         latest observed value per country & indicator
--   lpi_rankings            LPI survey-year scores with ranks
-- Note: the World Bank stores the 2023 LPI edition under year 2022.
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Q1. Top 5 African countries by LPI overall score in the latest LPI edition
-- -----------------------------------------------------------------------------
SELECT africa_rank,
       country_name,
       year,
       ROUND(lpi_overall, 2)        AS lpi_overall,
       ROUND(lpi_infrastructure, 2) AS lpi_infrastructure
FROM lpi_rankings
WHERE region = 'Africa'
  AND year = (SELECT MAX(year) FROM lpi_rankings)
ORDER BY africa_rank
LIMIT 5;


-- -----------------------------------------------------------------------------
-- Q2. LPI gap between each African country (latest available score) and USA
--     Negative gap = logistics performance below the US benchmark.
-- -----------------------------------------------------------------------------
WITH usa AS (
    SELECT lpi_overall AS usa_lpi FROM country_summary WHERE country_code = 'USA'
)
SELECT cs.country_name,
       cs.lpi_overall_year                              AS lpi_year,
       ROUND(cs.lpi_overall, 2)                         AS lpi_overall,
       ROUND(usa.usa_lpi, 2)                            AS usa_lpi,
       ROUND(cs.lpi_overall - usa.usa_lpi, 2)           AS lpi_gap_vs_usa,
       ROUND(100.0 * cs.lpi_overall / usa.usa_lpi, 1)   AS pct_of_usa
FROM country_summary cs
CROSS JOIN usa
WHERE cs.region = 'Africa'
ORDER BY lpi_gap_vs_usa DESC;


-- -----------------------------------------------------------------------------
-- Q3. Countries with the highest share of paved roads (latest observation)
-- -----------------------------------------------------------------------------
SELECT country_name,
       paved_roads_pct_year            AS observed_year,
       ROUND(paved_roads_pct, 1)       AS paved_roads_pct,
       ROUND(road_density, 1)          AS road_density_km_per_100sqkm
FROM country_summary
WHERE paved_roads_pct IS NOT NULL
ORDER BY paved_roads_pct DESC
LIMIT 10;


-- -----------------------------------------------------------------------------
-- Q4. Pearson correlation between road density and trade (% of GDP)
--     across African countries with both values (latest observations).
--     r = (n*Sxy - Sx*Sy) / sqrt((n*Sxx - Sx^2) * (n*Syy - Sy^2))
--     SQLite has no SQRT before 3.35, so r^2 is reported alongside the sign.
-- -----------------------------------------------------------------------------
WITH pairs AS (
    SELECT road_density AS x, trade_pct_gdp AS y
    FROM country_summary
    WHERE region = 'Africa'
      AND road_density IS NOT NULL
      AND trade_pct_gdp IS NOT NULL
),
agg AS (
    SELECT COUNT(*) AS n, SUM(x) AS sx, SUM(y) AS sy,
           SUM(x * x) AS sxx, SUM(y * y) AS syy, SUM(x * y) AS sxy
    FROM pairs
)
SELECT n                                                        AS countries,
       ROUND((n * sxy - sx * sy) * (n * sxy - sx * sy)
             / ((n * sxx - sx * sx) * (n * syy - sy * sy)), 4)  AS r_squared,
       CASE WHEN (n * sxy - sx * sy) >= 0 THEN 'positive' ELSE 'negative' END
                                                                AS direction
FROM agg;

-- Companion detail: the individual country pairs behind the correlation
SELECT country_name,
       ROUND(road_density, 1)  AS road_density_km_per_100sqkm,
       ROUND(trade_pct_gdp, 1) AS trade_pct_gdp,
       trade_openness_category
FROM country_summary
WHERE region = 'Africa' AND road_density IS NOT NULL AND trade_pct_gdp IS NOT NULL
ORDER BY road_density DESC;


-- -----------------------------------------------------------------------------
-- Q5. Edition-over-edition LPI change per country using window functions
-- -----------------------------------------------------------------------------
SELECT country_name,
       year,
       ROUND(lpi_overall, 2)                                           AS lpi_overall,
       LAG(year) OVER w                                                AS prev_year,
       ROUND(LAG(lpi_overall) OVER w, 2)                               AS prev_lpi,
       ROUND(lpi_overall - LAG(lpi_overall) OVER w, 2)                 AS lpi_change,
       ROUND(100.0 * (lpi_overall - LAG(lpi_overall) OVER w)
             / LAG(lpi_overall) OVER w, 1)                             AS pct_change,
       RANK() OVER (PARTITION BY year ORDER BY lpi_overall DESC)       AS rank_in_year
FROM lpi_rankings
WINDOW w AS (PARTITION BY country_code ORDER BY year)
ORDER BY country_name, year;


-- -----------------------------------------------------------------------------
-- Q6. Countries with the highest merchandise exports (latest year), with
--     share of the African total
-- -----------------------------------------------------------------------------
WITH latest AS (
    SELECT country_code, country_name, region, year, value
    FROM raw_indicators r
    WHERE indicator = 'merchandise_exports'
      AND year = (SELECT MAX(year) FROM raw_indicators r2
                  WHERE r2.indicator = 'merchandise_exports'
                    AND r2.country_code = r.country_code)
)
SELECT country_name,
       region,
       year,
       ROUND(value / 1e9, 2) AS exports_usd_bn,
       CASE WHEN region = 'Africa'
            THEN ROUND(100.0 * value / SUM(CASE WHEN region = 'Africa' THEN value END) OVER (), 1)
       END                   AS pct_of_africa_total
FROM latest
ORDER BY value DESC;


-- -----------------------------------------------------------------------------
-- Q7. Trade openness ranking (trade as % of GDP, latest year) with category
-- -----------------------------------------------------------------------------
SELECT DENSE_RANK() OVER (ORDER BY trade_pct_gdp DESC) AS openness_rank,
       country_name,
       trade_pct_gdp_year                              AS year,
       ROUND(trade_pct_gdp, 1)                         AS trade_pct_gdp,
       trade_openness_category,
       ROUND(gdp_per_capita, 0)                        AS gdp_per_capita_usd
FROM country_summary
WHERE trade_pct_gdp IS NOT NULL
ORDER BY openness_rank;


-- -----------------------------------------------------------------------------
-- Q8. Infrastructure sub-score vs overall LPI (latest edition per country)
--     A negative difference means infrastructure drags down overall logistics.
-- -----------------------------------------------------------------------------
WITH latest AS (
    SELECT *,
           ROW_NUMBER() OVER (PARTITION BY country_code ORDER BY year DESC) AS rn
    FROM lpi_rankings
)
SELECT country_name,
       year,
       ROUND(lpi_overall, 2)                       AS lpi_overall,
       ROUND(lpi_infrastructure, 2)                AS lpi_infrastructure,
       ROUND(lpi_infrastructure - lpi_overall, 2)  AS infra_minus_overall,
       CASE WHEN lpi_infrastructure < lpi_overall THEN 'Infrastructure lags'
            WHEN lpi_infrastructure > lpi_overall THEN 'Infrastructure leads'
            ELSE 'Balanced' END                    AS assessment
FROM latest
WHERE rn = 1
ORDER BY infra_minus_overall;
