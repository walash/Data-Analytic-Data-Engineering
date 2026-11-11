-- =============================================================================
-- AI-Driven Climate & Environmental Monitoring: Africa and USA
-- Analytical queries against data/climate_monitoring.db (SQLite >= 3.25)
--
-- Tables (created by src/loading/load.py):
--   climate_indicators  one row per country-year, wide indicators + features
--   country_profiles    one row per country, latest values and changes
--   ml_predictions      LinearRegression renewable-energy forecasts 2025-2027
--
-- Run:  sqlite3 -header -column data/climate_monitoring.db < sql/queries.sql
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Q1. Top 10 countries by renewable energy growth (window functions)
-- Uses FIRST_VALUE / LAST_VALUE over each country's history to compute the
-- change in renewable share (percentage points) between the first and last
-- observed year, then ranks countries with RANK().
-- -----------------------------------------------------------------------------
WITH bounds AS (
    SELECT DISTINCT
        country_code,
        country_name,
        region,
        FIRST_VALUE(year) OVER w                 AS first_year,
        FIRST_VALUE(renewable_energy_pct) OVER w AS first_value,
        LAST_VALUE(year) OVER w                  AS last_year,
        LAST_VALUE(renewable_energy_pct) OVER w  AS last_value
    FROM climate_indicators
    WHERE renewable_energy_pct IS NOT NULL
    WINDOW w AS (
        PARTITION BY country_code ORDER BY year
        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
    )
)
SELECT
    RANK() OVER (ORDER BY last_value - first_value DESC) AS growth_rank,
    country_name,
    region,
    first_year,
    ROUND(first_value, 2)               AS first_renewable_pct,
    last_year,
    ROUND(last_value, 2)                AS last_renewable_pct,
    ROUND(last_value - first_value, 2)  AS growth_pp
FROM bounds
ORDER BY growth_rank
LIMIT 10;


-- -----------------------------------------------------------------------------
-- Q2. Forest area trend comparison: Africa (average) vs USA
-- Yearly average forest cover for the African sample versus the USA, plus the
-- average annual forest loss rate (positive = forest lost).
-- -----------------------------------------------------------------------------
SELECT
    year,
    ROUND(AVG(CASE WHEN region = 'Africa' THEN forest_area_pct END), 2)
        AS africa_avg_forest_pct,
    ROUND(AVG(CASE WHEN region = 'USA' THEN forest_area_pct END), 2)
        AS usa_forest_pct,
    ROUND(AVG(CASE WHEN region = 'Africa' THEN forest_loss_rate END), 3)
        AS africa_avg_forest_loss_rate,
    ROUND(AVG(CASE WHEN region = 'USA' THEN forest_loss_rate END), 3)
        AS usa_forest_loss_rate
FROM climate_indicators
WHERE forest_area_pct IS NOT NULL
GROUP BY year
ORDER BY year;


-- -----------------------------------------------------------------------------
-- Q3. Countries with the highest electricity access improvement
-- Change in access to electricity (% of population) between each country's
-- first and latest observation.
-- -----------------------------------------------------------------------------
WITH access AS (
    SELECT
        country_code,
        country_name,
        region,
        year,
        electricity_access_pct,
        ROW_NUMBER() OVER (PARTITION BY country_code ORDER BY year ASC)  AS rn_first,
        ROW_NUMBER() OVER (PARTITION BY country_code ORDER BY year DESC) AS rn_last
    FROM climate_indicators
    WHERE electricity_access_pct IS NOT NULL
)
SELECT
    f.country_name,
    f.region,
    f.year                                                       AS start_year,
    ROUND(f.electricity_access_pct, 1)                           AS start_access_pct,
    l.year                                                       AS end_year,
    ROUND(l.electricity_access_pct, 1)                           AS end_access_pct,
    ROUND(l.electricity_access_pct - f.electricity_access_pct, 1) AS improvement_pp
FROM access f
JOIN access l
  ON l.country_code = f.country_code AND l.rn_last = 1
WHERE f.rn_first = 1
ORDER BY improvement_pp DESC
LIMIT 10;


-- -----------------------------------------------------------------------------
-- Q4. Correlation proxy: renewable energy vs electricity access
-- Pearson correlation computed in SQL from sums over country-years where both
-- indicators exist, overall and per region. A strongly negative value means
-- high renewable share (mostly traditional biomass) coincides with low grid
-- access - the "energy poverty paradox".
-- -----------------------------------------------------------------------------
WITH pairs AS (
    SELECT region, renewable_energy_pct AS x, electricity_access_pct AS y
    FROM climate_indicators
    WHERE renewable_energy_pct IS NOT NULL
      AND electricity_access_pct IS NOT NULL
),
stats AS (
    SELECT 'All countries' AS scope, COUNT(*) AS n,
           SUM(x) AS sx, SUM(y) AS sy, SUM(x * x) AS sxx,
           SUM(y * y) AS syy, SUM(x * y) AS sxy
    FROM pairs
    UNION ALL
    SELECT region, COUNT(*), SUM(x), SUM(y), SUM(x * x), SUM(y * y), SUM(x * y)
    FROM pairs
    GROUP BY region
)
SELECT
    scope,
    n AS observations,
    ROUND(sx / n, 2) AS avg_renewable_pct,
    ROUND(sy / n, 2) AS avg_access_pct,
    CASE
        WHEN (n * sxx - sx * sx) > 0 AND (n * syy - sy * sy) > 0
        THEN ROUND((n * sxy - sx * sy) /
                   (SQRT(n * sxx - sx * sx) * SQRT(n * syy - sy * sy)), 3)
    END AS pearson_r
FROM stats
ORDER BY n DESC;


-- -----------------------------------------------------------------------------
-- Q5. Year-over-year change in agricultural land
-- LAG() window function to compute the YoY change per country, filtered to
-- the largest absolute movements.
-- -----------------------------------------------------------------------------
WITH agri AS (
    SELECT
        country_name,
        region,
        year,
        agricultural_land_pct,
        LAG(agricultural_land_pct) OVER (
            PARTITION BY country_code ORDER BY year
        ) AS prev_agri_pct
    FROM climate_indicators
    WHERE agricultural_land_pct IS NOT NULL
)
SELECT
    country_name,
    region,
    year,
    ROUND(prev_agri_pct, 2)                          AS prev_agri_pct,
    ROUND(agricultural_land_pct, 2)                  AS agri_pct,
    ROUND(agricultural_land_pct - prev_agri_pct, 3)  AS yoy_change_pp
FROM agri
WHERE prev_agri_pct IS NOT NULL
ORDER BY ABS(agricultural_land_pct - prev_agri_pct) DESC
LIMIT 15;


-- -----------------------------------------------------------------------------
-- Q6. Countries below 50% electricity access (latest year per country)
-- -----------------------------------------------------------------------------
SELECT
    country_name,
    sub_region,
    latest_electricity_access_pct_year      AS latest_year,
    ROUND(latest_electricity_access_pct, 1) AS access_pct,
    ROUND(latest_renewable_energy_pct, 1)   AS renewable_pct,
    ROUND(change_electricity_access_pct, 1) AS access_change_pp
FROM country_profiles
WHERE latest_electricity_access_pct < 50
ORDER BY latest_electricity_access_pct ASC;


-- -----------------------------------------------------------------------------
-- Q7. ML predictions for renewable energy 2025-2027
-- Pivoted forecasts with the last observed value, model trend and fit quality.
-- -----------------------------------------------------------------------------
SELECT
    country_name,
    region,
    last_actual_year,
    ROUND(last_actual_value, 1) AS last_actual_pct,
    ROUND(MAX(CASE WHEN forecast_year = 2025 THEN predicted_renewable_energy_pct END), 1) AS pred_2025,
    ROUND(MAX(CASE WHEN forecast_year = 2026 THEN predicted_renewable_energy_pct END), 1) AS pred_2026,
    ROUND(MAX(CASE WHEN forecast_year = 2027 THEN predicted_renewable_energy_pct END), 1) AS pred_2027,
    ROUND(trend_slope_pp_per_year, 2) AS slope_pp_per_year,
    ROUND(r2_score, 2)                AS r2
FROM ml_predictions
GROUP BY country_code, country_name, region, last_actual_year,
         last_actual_value, trend_slope_pp_per_year, r2_score
ORDER BY pred_2027 DESC;


-- -----------------------------------------------------------------------------
-- Q8. Regional summary statistics
-- Latest-value averages and ranges by sub-region (Africa sub-regions vs USA).
-- -----------------------------------------------------------------------------
SELECT
    region,
    sub_region,
    COUNT(*)                                        AS countries,
    ROUND(AVG(latest_forest_area_pct), 1)           AS avg_forest_pct,
    ROUND(AVG(latest_renewable_energy_pct), 1)      AS avg_renewable_pct,
    ROUND(MIN(latest_renewable_energy_pct), 1)      AS min_renewable_pct,
    ROUND(MAX(latest_renewable_energy_pct), 1)      AS max_renewable_pct,
    ROUND(AVG(latest_agricultural_land_pct), 1)     AS avg_agri_land_pct,
    ROUND(AVG(latest_electricity_access_pct), 1)    AS avg_access_pct,
    ROUND(AVG(latest_electric_power_kwh_pc), 0)     AS avg_power_kwh_pc,
    ROUND(AVG(avg_forest_loss_rate), 3)             AS avg_forest_loss_rate
FROM country_profiles
GROUP BY region, sub_region
ORDER BY region, avg_renewable_pct DESC;
