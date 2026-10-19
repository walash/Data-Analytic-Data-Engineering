-- =============================================================================
-- Africa & USA Railway Networks — analytical queries
-- Target: SQLite warehouse data/railway_networks.db (built by src/loading/load.py)
-- Run:    sqlite3 -header -column data/railway_networks.db < sql/queries.sql
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Q1. Top African countries by rail network size (latest reported route-km)
--     Ranks every African country that reports rail lines and shows how the
--     network relates to income (km of rail per US$1,000 of GDP per capita).
-- -----------------------------------------------------------------------------
SELECT
    RANK() OVER (ORDER BY latest_rail_lines_km DESC)          AS network_rank,
    country_name,
    subregion,
    latest_rail_lines_km                                      AS rail_lines_km,
    latest_rail_lines_year                                    AS as_of_year,
    ROUND(latest_gdp_per_capita_usd, 0)                       AS gdp_per_capita_usd,
    ROUND(latest_rail_lines_km / (latest_gdp_per_capita_usd / 1000.0), 1)
                                                              AS rail_km_per_1k_gdp_pc
FROM country_profiles
WHERE is_africa = 1
  AND has_rail_network = 1
ORDER BY rail_lines_km DESC
LIMIT 15;


-- -----------------------------------------------------------------------------
-- Q2. Freight efficiency — rail freight carried per km of rail line
--     Freight density (million ton-km per route-km) shows how intensively each
--     network is used. Averages across all years with both measures available.
-- -----------------------------------------------------------------------------
SELECT
    p.country_name,
    p.region,
    COUNT(*)                                          AS years_observed,
    MIN(f.year)                                       AS first_year,
    MAX(f.year)                                       AS last_year,
    ROUND(AVG(f.rail_freight_million_tkm), 1)         AS avg_freight_million_tkm,
    ROUND(AVG(i.rail_lines_km_filled), 0)             AS avg_rail_lines_km,
    ROUND(AVG(f.freight_density_mtkm_per_km), 4)      AS avg_freight_density_mtkm_per_km,
    ROUND(AVG(f.freight_density_mtkm_per_km) * 1e6, 0) AS avg_tonnes_per_route_km
FROM rail_freight f
JOIN rail_infrastructure i ON i.country_code = f.country_code AND i.year = f.year
JOIN country_profiles p    ON p.country_code = f.country_code
WHERE f.freight_density_mtkm_per_km IS NOT NULL
GROUP BY p.country_code
ORDER BY avg_freight_density_mtkm_per_km DESC;


-- -----------------------------------------------------------------------------
-- Q3. Passenger trends — annual passenger-km with change vs. previous report
--     LAG() compares each year with the prior observation, exposing the
--     COVID-19 collapse in 2020 and the recovery path afterwards.
-- -----------------------------------------------------------------------------
SELECT
    p.country_name,
    s.year,
    ROUND(s.rail_passengers_million_km, 2)                         AS passengers_million_km,
    ROUND(LAG(s.rail_passengers_million_km) OVER w, 2)             AS previous_value,
    ROUND(100.0 * (s.rail_passengers_million_km - LAG(s.rail_passengers_million_km) OVER w)
          / LAG(s.rail_passengers_million_km) OVER w, 1)           AS change_pct,
    ROUND(s.passenger_density_mpkm_per_km, 4)                      AS passenger_density_mpkm_per_km
FROM rail_passengers s
JOIN country_profiles p ON p.country_code = s.country_code
WINDOW w AS (PARTITION BY s.country_code ORDER BY s.year)
ORDER BY p.country_name, s.year;


-- -----------------------------------------------------------------------------
-- Q4. LPI infrastructure score vs. rail network — correlation inputs
--     Groups countries into "has rail data" vs. "no rail data" and computes the
--     Pearson correlation between latest rail route-km and LPI infrastructure
--     score for countries that report both.
-- -----------------------------------------------------------------------------
WITH paired AS (
    SELECT latest_rail_lines_km AS x, latest_lpi_infrastructure_score AS y
    FROM country_profiles
    WHERE latest_rail_lines_km IS NOT NULL
      AND latest_lpi_infrastructure_score IS NOT NULL
),
stats AS (
    SELECT COUNT(*) AS n, AVG(x) AS mx, AVG(y) AS my FROM paired
)
SELECT
    'Pearson r (rail km vs LPI infra)'                         AS metric,
    s.n                                                        AS countries,
    ROUND(SUM((p.x - s.mx) * (p.y - s.my)) /
          (SQRT(SUM((p.x - s.mx) * (p.x - s.mx))) * SQRT(SUM((p.y - s.my) * (p.y - s.my)))), 3)
                                                               AS value
FROM paired p CROSS JOIN stats s
UNION ALL
SELECT
    CASE has_rail_network WHEN 1 THEN 'Avg LPI infra - reports rail lines'
                          ELSE 'Avg LPI infra - no rail lines reported' END,
    COUNT(*),
    ROUND(AVG(latest_lpi_infrastructure_score), 3)
FROM country_profiles
WHERE latest_lpi_infrastructure_score IS NOT NULL
GROUP BY has_rail_network;


-- -----------------------------------------------------------------------------
-- Q5. Year-over-year growth across rail lines, freight and passengers
--     Only consecutive-year comparisons are included (pre-computed in the
--     transformation stage) so multi-year reporting gaps do not distort growth.
-- -----------------------------------------------------------------------------
SELECT
    p.country_name,
    i.year,
    ROUND(i.rail_lines_yoy_pct, 2)       AS rail_lines_yoy_pct,
    ROUND(f.rail_freight_yoy_pct, 2)     AS freight_yoy_pct,
    ROUND(s.rail_passengers_yoy_pct, 2)  AS passengers_yoy_pct
FROM rail_infrastructure i
JOIN country_profiles p      ON p.country_code = i.country_code
LEFT JOIN rail_freight f     ON f.country_code = i.country_code AND f.year = i.year
LEFT JOIN rail_passengers s  ON s.country_code = i.country_code AND s.year = i.year
WHERE i.rail_lines_yoy_pct IS NOT NULL
   OR f.rail_freight_yoy_pct IS NOT NULL
   OR s.rail_passengers_yoy_pct IS NOT NULL
ORDER BY p.country_name, i.year;


-- -----------------------------------------------------------------------------
-- Q6. USA vs. Africa comparison
--     Aggregates the latest values per region. Africa is summed across all
--     reporting countries; the USA row appears when the US benchmark is present
--     in the ingested extract (country_code = 'US').
-- -----------------------------------------------------------------------------
SELECT
    region,
    COUNT(*)                                             AS countries,
    SUM(has_rail_network)                                AS countries_reporting_rail,
    ROUND(SUM(latest_rail_lines_km), 0)                  AS total_rail_lines_km,
    ROUND(SUM(latest_rail_freight_million_tkm), 0)       AS total_freight_million_tkm,
    ROUND(SUM(latest_rail_passengers_million_km), 0)     AS total_passengers_million_km,
    ROUND(SUM(latest_rail_freight_million_tkm)
          / SUM(CASE WHEN latest_rail_freight_million_tkm IS NOT NULL
                     THEN latest_rail_lines_km END), 4)  AS freight_density_mtkm_per_km,
    ROUND(AVG(latest_lpi_infrastructure_score), 2)       AS avg_lpi_infrastructure,
    ROUND(AVG(latest_gdp_per_capita_usd), 0)             AS avg_gdp_per_capita_usd
FROM country_profiles
WHERE region IN ('Africa', 'USA')
GROUP BY region
ORDER BY region;


-- -----------------------------------------------------------------------------
-- Q7. Countries with declining rail networks
--     Compares the earliest and latest reported route-km per country and flags
--     any contraction (track closures, re-measurement or abandoned lines).
-- -----------------------------------------------------------------------------
WITH bounds AS (
    SELECT country_code,
           MIN(year) AS first_year,
           MAX(year) AS last_year
    FROM rail_infrastructure
    WHERE rail_lines_km IS NOT NULL
    GROUP BY country_code
    HAVING COUNT(*) >= 2
)
SELECT
    p.country_name,
    b.first_year,
    first_obs.rail_lines_km                                         AS first_km,
    b.last_year,
    last_obs.rail_lines_km                                          AS last_km,
    ROUND(last_obs.rail_lines_km - first_obs.rail_lines_km, 2)      AS change_km,
    ROUND(100.0 * (last_obs.rail_lines_km - first_obs.rail_lines_km)
          / first_obs.rail_lines_km, 2)                             AS change_pct,
    CASE WHEN last_obs.rail_lines_km < first_obs.rail_lines_km THEN 'Declining'
         WHEN last_obs.rail_lines_km > first_obs.rail_lines_km THEN 'Expanding'
         ELSE 'Stable' END                                          AS trend
FROM bounds b
JOIN rail_infrastructure first_obs ON first_obs.country_code = b.country_code
                                  AND first_obs.year = b.first_year
JOIN rail_infrastructure last_obs  ON last_obs.country_code = b.country_code
                                  AND last_obs.year = b.last_year
JOIN country_profiles p            ON p.country_code = b.country_code
ORDER BY change_pct ASC;


-- -----------------------------------------------------------------------------
-- Q8. Regional groupings — rail coverage by African sub-region (plus USA)
--     Share of countries reporting any rail network, total route-km and the
--     average LPI infrastructure score for each sub-region.
-- -----------------------------------------------------------------------------
SELECT
    subregion,
    COUNT(*)                                                    AS countries_in_extract,
    SUM(has_rail_network)                                       AS countries_with_rail_data,
    ROUND(100.0 * SUM(has_rail_network) / COUNT(*), 1)          AS rail_data_coverage_pct,
    ROUND(COALESCE(SUM(latest_rail_lines_km), 0), 0)            AS total_rail_lines_km,
    ROUND(AVG(latest_lpi_infrastructure_score), 2)              AS avg_lpi_infrastructure,
    ROUND(AVG(latest_gdp_per_capita_usd), 0)                    AS avg_gdp_per_capita_usd,
    GROUP_CONCAT(CASE WHEN has_rail_network = 1 THEN country_name END, ', ')
                                                                AS rail_countries
FROM country_profiles
GROUP BY subregion
ORDER BY total_rail_lines_km DESC;
