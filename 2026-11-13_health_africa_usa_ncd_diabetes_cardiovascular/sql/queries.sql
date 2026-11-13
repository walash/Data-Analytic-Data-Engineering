-- =============================================================================
-- NCD Burden (Diabetes & Cardiovascular Disease) - Africa vs USA
-- Analytical queries for data/ncd_health.db (built by src/loading/load.py)
--
-- Schema:
--   dim_country(iso3, country_id, country_name, region_group, africa_subregion, is_aggregate)
--   dim_indicator(indicator_code, indicator_key, indicator_name, first_year, last_year, ...)
--   fact_ncd_observation(iso3, indicator_code, year, value, prev_year, prev_value,
--                        change_pp, change_pp_per_year, is_latest, africa_rank)
--   v_africa_usa_ncd  -> facts joined to both dimensions (Africa, USA, Benchmark)
--
-- Indicators:
--   SH.STA.DIAB.ZS  diabetes_prevalence  (% of population ages 20-79)
--   SH.DTH.NCOM.ZS  ncd_deaths           (NCDs as % of all deaths)
--   SH.DYN.NCOM.ZS  ncd_mortality_30_70  (prob. of dying 30-70 from CVD/cancer/diabetes/CRD)
--
-- Run:  sqlite3 data/ncd_health.db < sql/queries.sql
-- =============================================================================

.headers on
.mode column


-- -----------------------------------------------------------------------------
-- Q1. Latest-year snapshot: Africa (country distribution) vs USA vs benchmarks
--     For every indicator, summarise the most recent value of each entity by
--     region group. Shows how the US compares with the African average/range.
-- -----------------------------------------------------------------------------
SELECT
    indicator_key,
    region_group,
    COUNT(*)                 AS n_entities,
    ROUND(AVG(value), 2)     AS avg_value,
    ROUND(MIN(value), 2)     AS min_value,
    ROUND(MAX(value), 2)     AS max_value,
    MAX(year)                AS latest_year
FROM v_africa_usa_ncd
WHERE is_latest = 1
GROUP BY indicator_key, region_group
ORDER BY indicator_key, region_group;


-- -----------------------------------------------------------------------------
-- Q2. Top 10 African countries by diabetes prevalence (latest year) and their
--     gap to the United States (percentage points). CTE + cross join to USA.
-- -----------------------------------------------------------------------------
WITH usa AS (
    SELECT f.value AS usa_value, f.year AS usa_year
    FROM fact_ncd_observation f
    WHERE f.iso3 = 'USA' AND f.indicator_code = 'SH.STA.DIAB.ZS' AND f.is_latest = 1
)
SELECT
    c.country_name,
    c.africa_subregion,
    f.year,
    ROUND(f.value, 2)                    AS diabetes_pct,
    ROUND(u.usa_value, 2)                AS usa_diabetes_pct,
    ROUND(f.value - u.usa_value, 2)      AS gap_vs_usa_pp
FROM fact_ncd_observation f
JOIN dim_country c ON c.iso3 = f.iso3
CROSS JOIN usa u
WHERE c.region_group = 'Africa'
  AND f.indicator_code = 'SH.STA.DIAB.ZS'
  AND f.is_latest = 1
ORDER BY f.value DESC
LIMIT 10;


-- -----------------------------------------------------------------------------
-- Q3. Diabetes trajectory 2011 -> 2024: change per African country and USA.
--     Uses LAG() over each country's time series to compute the change between
--     consecutive survey years, then keeps the most recent change.
-- -----------------------------------------------------------------------------
WITH series AS (
    SELECT
        v.iso3,
        v.country_name,
        v.region_group,
        v.year,
        v.value,
        LAG(v.year)  OVER (PARTITION BY v.iso3 ORDER BY v.year) AS prior_year,
        LAG(v.value) OVER (PARTITION BY v.iso3 ORDER BY v.year) AS prior_value
    FROM v_africa_usa_ncd v
    WHERE v.indicator_key = 'diabetes_prevalence'
      AND v.region_group IN ('Africa', 'USA')
)
SELECT
    country_name,
    region_group,
    prior_year,
    ROUND(prior_value, 2)                 AS prior_pct,
    year                                  AS latest_year,
    ROUND(value, 2)                       AS latest_pct,
    ROUND(value - prior_value, 2)         AS change_pp,
    ROUND(100.0 * (value - prior_value) / NULLIF(prior_value, 0), 1) AS change_pct
FROM series
WHERE prior_value IS NOT NULL
  AND year = (SELECT MAX(s2.year) FROM series s2 WHERE s2.iso3 = series.iso3)
ORDER BY change_pp DESC
LIMIT 15;


-- -----------------------------------------------------------------------------
-- Q4. Rank African countries within their sub-region by latest diabetes
--     prevalence (RANK window) and show each country's share of the sub-region
--     leader's value. Returns the top 3 per sub-region.
-- -----------------------------------------------------------------------------
WITH ranked AS (
    SELECT
        c.africa_subregion,
        c.country_name,
        f.value,
        RANK()     OVER (PARTITION BY c.africa_subregion ORDER BY f.value DESC) AS subregion_rank,
        MAX(f.value) OVER (PARTITION BY c.africa_subregion)                     AS subregion_max,
        COUNT(*)   OVER (PARTITION BY c.africa_subregion)                       AS n_countries
    FROM fact_ncd_observation f
    JOIN dim_country c ON c.iso3 = f.iso3
    WHERE c.region_group = 'Africa'
      AND f.indicator_code = 'SH.STA.DIAB.ZS'
      AND f.is_latest = 1
)
SELECT
    africa_subregion,
    subregion_rank,
    country_name,
    ROUND(value, 2)                        AS diabetes_pct,
    ROUND(100.0 * value / subregion_max, 1) AS pct_of_leader,
    n_countries
FROM ranked
WHERE subregion_rank <= 3
ORDER BY africa_subregion, subregion_rank;


-- -----------------------------------------------------------------------------
-- Q5. Sub-regional diabetes burden relative to the USA.
--     Average latest prevalence per African sub-region, expressed as a ratio of
--     the US value (1.00 = same burden as the US).
-- -----------------------------------------------------------------------------
SELECT
    c.africa_subregion,
    COUNT(*)                                        AS n_countries,
    ROUND(AVG(f.value), 2)                          AS avg_diabetes_pct,
    ROUND(AVG(f.value) / (
        SELECT value FROM fact_ncd_observation
        WHERE iso3 = 'USA' AND indicator_code = 'SH.STA.DIAB.ZS' AND is_latest = 1
    ), 2)                                           AS ratio_to_usa,
    SUM(CASE WHEN f.value > 10 THEN 1 ELSE 0 END)   AS countries_above_10pct
FROM fact_ncd_observation f
JOIN dim_country c ON c.iso3 = f.iso3
WHERE c.region_group = 'Africa'
  AND f.indicator_code = 'SH.STA.DIAB.ZS'
  AND f.is_latest = 1
GROUP BY c.africa_subregion
ORDER BY avg_diabetes_pct DESC;


-- -----------------------------------------------------------------------------
-- Q6. Epidemiological transition: NCD share of all deaths over time for
--     African regional aggregates vs North America and the World.
--     LAG() for period change and a running average over the series.
-- -----------------------------------------------------------------------------
SELECT
    c.country_name                                    AS region,
    f.year,
    ROUND(f.value, 2)                                 AS ncd_share_of_deaths_pct,
    ROUND(f.value - LAG(f.value) OVER w, 2)           AS change_vs_prev_pp,
    ROUND(AVG(f.value) OVER (w ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 2)
                                                      AS running_avg_pct
FROM fact_ncd_observation f
JOIN dim_country c ON c.iso3 = f.iso3
WHERE f.indicator_code = 'SH.DTH.NCOM.ZS'
  AND f.iso3 IN ('SSF', 'AFE', 'AFW', 'MEA', 'NAC', 'USA', 'WLD')
WINDOW w AS (PARTITION BY f.iso3 ORDER BY f.year)
ORDER BY region, f.year;


-- -----------------------------------------------------------------------------
-- Q7. African countries whose NCD share of deaths (latest year) exceeds the
--     Sub-Saharan Africa benchmark - i.e. countries further along the shift
--     from infectious to chronic disease mortality. Join to the benchmark row.
-- -----------------------------------------------------------------------------
WITH ssa AS (
    SELECT year, value AS ssa_value
    FROM fact_ncd_observation
    WHERE iso3 = 'SSF' AND indicator_code = 'SH.DTH.NCOM.ZS'
)
SELECT
    c.country_name,
    c.africa_subregion,
    f.year,
    ROUND(f.value, 2)               AS ncd_share_pct,
    ROUND(s.ssa_value, 2)           AS ssa_benchmark_pct,
    ROUND(f.value - s.ssa_value, 2) AS excess_pp,
    ROUND(f.change_pp_per_year, 2)  AS trend_pp_per_year
FROM fact_ncd_observation f
JOIN dim_country c ON c.iso3 = f.iso3
JOIN ssa s ON s.year = f.year
WHERE c.region_group = 'Africa'
  AND f.indicator_code = 'SH.DTH.NCOM.ZS'
  AND f.is_latest = 1
  AND f.value > s.ssa_value
ORDER BY excess_pp DESC;


-- -----------------------------------------------------------------------------
-- Q8. "Double burden" matrix: join each entity's latest diabetes prevalence
--     with its latest NCD death share and premature NCD mortality (30-70).
--     NTILE(4) assigns diabetes quartiles across Africa + USA so countries
--     high on both metabolic risk and NCD mortality can be prioritised.
-- -----------------------------------------------------------------------------
WITH latest AS (
    SELECT iso3, indicator_key, value
    FROM v_africa_usa_ncd
    WHERE is_latest = 1 AND region_group IN ('Africa', 'USA')
),
pivoted AS (
    SELECT
        l.iso3,
        MAX(CASE WHEN l.indicator_key = 'diabetes_prevalence' THEN l.value END) AS diabetes_pct,
        MAX(CASE WHEN l.indicator_key = 'ncd_deaths'          THEN l.value END) AS ncd_death_share_pct,
        MAX(CASE WHEN l.indicator_key = 'ncd_mortality_30_70' THEN l.value END) AS ncd_mort_30_70_pct
    FROM latest l
    GROUP BY l.iso3
)
SELECT
    c.country_name,
    c.region_group,
    ROUND(p.diabetes_pct, 2)        AS diabetes_pct,
    ROUND(p.ncd_death_share_pct, 2) AS ncd_death_share_pct,
    ROUND(p.ncd_mort_30_70_pct, 2)  AS ncd_mort_30_70_pct,
    NTILE(4) OVER (ORDER BY p.diabetes_pct DESC) AS diabetes_quartile,
    CASE
        WHEN p.diabetes_pct >= 7 AND p.ncd_death_share_pct >= 50 THEN 'High diabetes + NCD-dominant mortality'
        WHEN p.diabetes_pct >= 7 THEN 'High diabetes'
        WHEN p.ncd_death_share_pct >= 50 THEN 'NCD-dominant mortality'
        ELSE 'Lower NCD burden / data gap'
    END AS burden_profile
FROM pivoted p
JOIN dim_country c ON c.iso3 = p.iso3
WHERE p.diabetes_pct IS NOT NULL
ORDER BY diabetes_quartile, p.diabetes_pct DESC
LIMIT 25;
