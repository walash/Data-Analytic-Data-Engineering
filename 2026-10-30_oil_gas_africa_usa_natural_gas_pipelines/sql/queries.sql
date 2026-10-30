-- =============================================================================
-- Natural Gas Pipelines — Africa & USA Trade Flows and Infrastructure
-- Analytical queries for data/natural_gas.db (SQLite, built by src/loading/load.py)
-- Run:  sqlite3 -header -column data/natural_gas.db < sql/queries.sql
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Q1. Gas-power dependency: latest share of electricity generated from natural
--     gas for every focus country, ranked from most to least dependent.
-- -----------------------------------------------------------------------------
WITH latest AS (
    SELECT iso3, MAX(year) AS year
    FROM gas_indicators
    WHERE indicator_key = 'ngas_electricity_pct'
    GROUP BY iso3
)
SELECT c.country_name,
       c.region,
       g.year,
       ROUND(g.value, 2) AS gas_share_of_electricity_pct,
       RANK() OVER (ORDER BY g.value DESC) AS dependency_rank
FROM gas_indicators g
JOIN latest l   ON l.iso3 = g.iso3 AND l.year = g.year
JOIN countries c ON c.iso3 = g.iso3
WHERE g.indicator_key = 'ngas_electricity_pct'
  AND c.is_focus = 1
ORDER BY dependency_rank;


-- -----------------------------------------------------------------------------
-- Q2. Africa vs USA: average of each indicator by region for the latest year
--     in which both regions report data.
-- -----------------------------------------------------------------------------
WITH region_years AS (
    SELECT g.indicator_key, g.year
    FROM gas_indicators g
    JOIN countries c ON c.iso3 = g.iso3
    GROUP BY g.indicator_key, g.year
    HAVING COUNT(DISTINCT c.region) = 2
),
latest_common AS (
    SELECT indicator_key, MAX(year) AS year FROM region_years GROUP BY indicator_key
)
SELECT g.indicator_key,
       lc.year,
       c.region,
       COUNT(*)              AS countries_reporting,
       ROUND(AVG(g.value), 2) AS avg_value,
       ROUND(MIN(g.value), 2) AS min_value,
       ROUND(MAX(g.value), 2) AS max_value
FROM gas_indicators g
JOIN latest_common lc ON lc.indicator_key = g.indicator_key AND lc.year = g.year
JOIN countries c      ON c.iso3 = g.iso3
GROUP BY g.indicator_key, lc.year, c.region
ORDER BY g.indicator_key, c.region;


-- -----------------------------------------------------------------------------
-- Q3. Natural gas rents trend: year-over-year change in gas rents (% of GDP)
--     for focus countries, using LAG over consecutive years.
-- -----------------------------------------------------------------------------
SELECT c.country_name,
       g.year,
       ROUND(g.value, 3) AS gas_rents_pct_gdp,
       ROUND(g.value - LAG(g.value) OVER (PARTITION BY g.iso3 ORDER BY g.year), 3) AS yoy_change_pp
FROM gas_indicators g
JOIN countries c ON c.iso3 = g.iso3
WHERE g.indicator_key = 'ngas_rents_gdp_pct'
  AND c.is_focus = 1
ORDER BY c.country_name, g.year;


-- -----------------------------------------------------------------------------
-- Q4. Peak gas-rent year per country: the year each focus country extracted
--     the most value from natural gas relative to GDP.
-- -----------------------------------------------------------------------------
SELECT country_name, year AS peak_year, ROUND(value, 3) AS peak_gas_rents_pct_gdp
FROM (
    SELECT c.country_name, g.year, g.value,
           ROW_NUMBER() OVER (PARTITION BY g.iso3 ORDER BY g.value DESC, g.year DESC) AS rn
    FROM gas_indicators g
    JOIN countries c ON c.iso3 = g.iso3
    WHERE g.indicator_key = 'ngas_rents_gdp_pct' AND c.is_focus = 1
)
WHERE rn = 1
ORDER BY peak_gas_rents_pct_gdp DESC;


-- -----------------------------------------------------------------------------
-- Q5. Hydrocarbon export concentration: countries where fuels made up more than
--     50% of merchandise exports in their latest reported year.
-- -----------------------------------------------------------------------------
WITH latest AS (
    SELECT iso3, MAX(year) AS year
    FROM gas_indicators
    WHERE indicator_key = 'fuel_exports_pct'
    GROUP BY iso3
)
SELECT c.country_name, c.region, g.year, ROUND(g.value, 2) AS fuel_exports_pct
FROM gas_indicators g
JOIN latest l    ON l.iso3 = g.iso3 AND l.year = g.year
JOIN countries c ON c.iso3 = g.iso3
WHERE g.indicator_key = 'fuel_exports_pct'
  AND g.value > 50
ORDER BY g.value DESC;


-- -----------------------------------------------------------------------------
-- Q6. Energy-dependency composite: average of each country's latest gas
--     electricity share and fuel export share (both 0-100 scales), plus gas
--     rents scaled x10 to bring them onto a comparable range.
-- -----------------------------------------------------------------------------
WITH latest_vals AS (
    SELECT g.iso3, g.indicator_key, g.value,
           ROW_NUMBER() OVER (PARTITION BY g.iso3, g.indicator_key ORDER BY g.year DESC) AS rn
    FROM gas_indicators g
),
pivoted AS (
    SELECT iso3,
           MAX(CASE WHEN indicator_key = 'ngas_electricity_pct' THEN value END) AS elec,
           MAX(CASE WHEN indicator_key = 'fuel_exports_pct'     THEN value END) AS fuel,
           MAX(CASE WHEN indicator_key = 'ngas_rents_gdp_pct'   THEN value END) AS rents
    FROM latest_vals
    WHERE rn = 1
    GROUP BY iso3
)
SELECT c.country_name,
       c.region,
       ROUND(p.elec, 1)  AS gas_power_pct,
       ROUND(p.fuel, 1)  AS fuel_exports_pct,
       ROUND(p.rents, 2) AS gas_rents_pct_gdp,
       ROUND((COALESCE(p.elec, 0) + COALESCE(p.fuel, 0) + MIN(COALESCE(p.rents, 0) * 10, 100))
             / ((p.elec IS NOT NULL) + (p.fuel IS NOT NULL) + (p.rents IS NOT NULL)), 1) AS dependency_score
FROM pivoted p
JOIN countries c ON c.iso3 = p.iso3
WHERE c.is_focus = 1
ORDER BY dependency_score DESC;


-- -----------------------------------------------------------------------------
-- Q7. Pipeline export capacity by origin country, joined to the origin's
--     latest gas-power share to contrast export infrastructure with domestic use.
-- -----------------------------------------------------------------------------
SELECT p.origin_iso3,
       COALESCE(c.country_name, p.origin_iso3)      AS origin_country,
       COUNT(*)                                     AS pipelines,
       ROUND(SUM(p.capacity_bcm_y), 1)              AS total_capacity_bcm_y,
       ROUND(SUM(p.length_km), 0)                   AS total_length_km,
       (SELECT ROUND(g.value, 1)
          FROM gas_indicators g
         WHERE g.iso3 = p.origin_iso3 AND g.indicator_key = 'ngas_electricity_pct'
         ORDER BY g.year DESC LIMIT 1)              AS latest_gas_power_pct
FROM pipeline_infrastructure p
LEFT JOIN countries c ON c.iso3 = p.origin_iso3
GROUP BY p.origin_iso3, c.country_name
ORDER BY total_capacity_bcm_y DESC;


-- -----------------------------------------------------------------------------
-- Q8. Pipeline catalogue by status and corridor, with capacity share of the
--     African corridors (USA domestic trunkline excluded from the share).
-- -----------------------------------------------------------------------------
SELECT region,
       status,
       COUNT(*)                         AS pipelines,
       ROUND(SUM(capacity_bcm_y), 1)    AS capacity_bcm_y,
       ROUND(100.0 * SUM(capacity_bcm_y) /
             (SELECT SUM(capacity_bcm_y) FROM pipeline_infrastructure WHERE origin_iso3 <> 'USA'), 1)
                                        AS pct_of_african_capacity
FROM pipeline_infrastructure
WHERE origin_iso3 <> 'USA'
GROUP BY region, status
ORDER BY capacity_bcm_y DESC;


-- -----------------------------------------------------------------------------
-- Q9. Biggest movers in gas-fired power: change in gas share of electricity
--     between each country's first and last reported year.
-- -----------------------------------------------------------------------------
WITH bounds AS (
    SELECT iso3, MIN(year) AS first_year, MAX(year) AS last_year
    FROM gas_indicators
    WHERE indicator_key = 'ngas_electricity_pct'
    GROUP BY iso3
)
SELECT c.country_name,
       b.first_year,
       b.last_year,
       ROUND(f.value, 2)           AS first_value,
       ROUND(l.value, 2)           AS last_value,
       ROUND(l.value - f.value, 2) AS change_pp
FROM bounds b
JOIN gas_indicators f ON f.iso3 = b.iso3 AND f.year = b.first_year AND f.indicator_key = 'ngas_electricity_pct'
JOIN gas_indicators l ON l.iso3 = b.iso3 AND l.year = b.last_year  AND l.indicator_key = 'ngas_electricity_pct'
JOIN countries c      ON c.iso3 = b.iso3
WHERE b.last_year > b.first_year
ORDER BY ABS(l.value - f.value) DESC;


-- -----------------------------------------------------------------------------
-- Q10. Data coverage audit: number of years reported per indicator for every
--      country, highlighting gaps that limit cross-country comparison.
-- -----------------------------------------------------------------------------
SELECT c.country_name,
       c.region,
       c.is_focus,
       SUM(CASE WHEN g.indicator_key = 'ngas_electricity_pct' THEN 1 ELSE 0 END) AS years_gas_power,
       SUM(CASE WHEN g.indicator_key = 'ngas_rents_gdp_pct'   THEN 1 ELSE 0 END) AS years_gas_rents,
       SUM(CASE WHEN g.indicator_key = 'fuel_exports_pct'     THEN 1 ELSE 0 END) AS years_fuel_exports,
       MIN(g.year) AS first_year,
       MAX(g.year) AS last_year
FROM countries c
LEFT JOIN gas_indicators g ON g.iso3 = c.iso3
GROUP BY c.iso3, c.country_name, c.region, c.is_focus
ORDER BY c.is_focus DESC, c.country_name;
