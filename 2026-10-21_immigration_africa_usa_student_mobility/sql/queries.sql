-- =============================================================================
-- African Student Mobility to/from the USA - Analytical Queries
-- Database : data/student_mobility.db  (built by src/loading/load.py)
-- Tables   : countries, indicators, indicator_observations,
--            country_year_metrics, mobility_snapshot, pipeline_runs
-- Run      : sqlite3 data/student_mobility.db < sql/queries.sql
-- Requires : SQLite >= 3.25 (window functions)
-- =============================================================================

.headers on
.mode column

-- -----------------------------------------------------------------------------
-- Q1. Student Mobility Pressure ranking (latest year) with sub-regional rank.
--     Higher index = stronger push factors for outbound tertiary study.
-- -----------------------------------------------------------------------------
WITH latest AS (
    SELECT MAX(year) AS yr
    FROM country_year_metrics
    WHERE is_africa = 1 AND mobility_pressure_index IS NOT NULL
)
SELECT
    m.year,
    m.iso3,
    m.country_name,
    m.sub_region,
    ROUND(m.mobility_pressure_index, 1)                                   AS pressure_index,
    RANK() OVER (ORDER BY m.mobility_pressure_index DESC)                 AS africa_rank,
    RANK() OVER (PARTITION BY m.sub_region
                 ORDER BY m.mobility_pressure_index DESC)                 AS sub_region_rank,
    m.enrollment_reference_year
FROM country_year_metrics m
JOIN latest l ON m.year = l.yr
WHERE m.is_africa = 1
ORDER BY africa_rank;

-- -----------------------------------------------------------------------------
-- Q2. Tertiary enrollment gap vs the USA (same year), latest common observation
--     per country, plus how many US students' "share" each African rate equals.
-- -----------------------------------------------------------------------------
WITH paired AS (
    SELECT
        a.iso3,
        a.country_name,
        a.year,
        a.tertiary_enrollment                      AS africa_enrollment,
        u.tertiary_enrollment                      AS usa_enrollment,
        ROW_NUMBER() OVER (PARTITION BY a.iso3 ORDER BY a.year DESC) AS rn
    FROM country_year_metrics a
    JOIN country_year_metrics u
      ON u.year = a.year AND u.iso3 = 'USA'
    WHERE a.is_africa = 1
      AND a.tertiary_enrollment IS NOT NULL
      AND u.tertiary_enrollment IS NOT NULL
)
SELECT
    iso3,
    country_name,
    year                                               AS latest_common_year,
    ROUND(africa_enrollment, 1)                        AS africa_enrollment_pct,
    ROUND(usa_enrollment, 1)                           AS usa_enrollment_pct,
    ROUND(usa_enrollment - africa_enrollment, 1)       AS gap_pp,
    ROUND(100.0 * africa_enrollment / usa_enrollment, 1) AS pct_of_usa_rate
FROM paired
WHERE rn = 1
ORDER BY gap_pp DESC;

-- -----------------------------------------------------------------------------
-- Q3. Enrollment momentum: 5-year change in tertiary enrollment using LAG over
--     observed (non-null) values, and a 3-observation moving average.
-- -----------------------------------------------------------------------------
WITH obs AS (
    SELECT
        o.iso3,
        c.country_name,
        o.year,
        o.value AS enrollment,
        LAG(o.value, 5) OVER (PARTITION BY o.iso3 ORDER BY o.year) AS enrollment_5_obs_ago,
        LAG(o.year, 5)  OVER (PARTITION BY o.iso3 ORDER BY o.year) AS year_5_obs_ago,
        AVG(o.value) OVER (PARTITION BY o.iso3 ORDER BY o.year
                           ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) AS moving_avg_3,
        ROW_NUMBER() OVER (PARTITION BY o.iso3 ORDER BY o.year DESC) AS rn
    FROM indicator_observations o
    JOIN countries c ON c.iso3 = o.iso3
    WHERE o.indicator_key = 'tertiary_enrollment'
)
SELECT
    iso3,
    country_name,
    year                                   AS latest_year,
    ROUND(enrollment, 1)                   AS enrollment_pct,
    year_5_obs_ago,
    ROUND(enrollment - enrollment_5_obs_ago, 1) AS change_pp,
    ROUND(moving_avg_3, 1)                 AS moving_avg_3
FROM obs
WHERE rn = 1
ORDER BY change_pp DESC;

-- -----------------------------------------------------------------------------
-- Q4. Remittances: top receivers in the most recent year reported by at least
--     12 of the 15 African countries, share of total, cumulative (Pareto)
--     share, and total growth since 2015. Uses only core SQLite functions.
-- -----------------------------------------------------------------------------
WITH coverage AS (
    SELECT o.year, COUNT(*) AS n_countries
    FROM indicator_observations o
    JOIN countries c ON c.iso3 = o.iso3 AND c.is_africa = 1
    WHERE o.indicator_key = 'remittances'
    GROUP BY o.year
),
latest AS (
    SELECT MAX(year) AS yr FROM coverage WHERE n_countries >= 12
),
cur AS (
    SELECT o.iso3, o.year, o.value AS remit_now
    FROM indicator_observations o
    JOIN latest l ON o.year = l.yr
    JOIN countries c ON c.iso3 = o.iso3 AND c.is_africa = 1
    WHERE o.indicator_key = 'remittances'
),
base AS (
    SELECT iso3, value AS remit_2015
    FROM indicator_observations
    WHERE indicator_key = 'remittances' AND year = 2015
)
SELECT
    cur.year,
    c.iso3,
    c.country_name,
    ROUND(cur.remit_now / 1e9, 2)                                       AS remittances_bn_usd,
    ROUND(100.0 * cur.remit_now / SUM(cur.remit_now) OVER (), 1)        AS share_pct,
    ROUND(100.0 * SUM(cur.remit_now) OVER (ORDER BY cur.remit_now DESC
              ROWS UNBOUNDED PRECEDING) / SUM(cur.remit_now) OVER (), 1) AS cumulative_share_pct,
    ROUND(100.0 * (cur.remit_now - base.remit_2015) / base.remit_2015, 1) AS growth_since_2015_pct
FROM cur
JOIN countries c ON c.iso3 = cur.iso3
LEFT JOIN base ON base.iso3 = cur.iso3
ORDER BY cur.remit_now DESC;

-- -----------------------------------------------------------------------------
-- Q5. Sub-regional aggregates per year (population-weighted where relevant):
--     tertiary enrollment, GDP per capita, education spend and net migration.
-- -----------------------------------------------------------------------------
SELECT
    sub_region,
    year,
    COUNT(*)                                                         AS countries,
    ROUND(SUM(tertiary_enrollment * population)
          / NULLIF(SUM(CASE WHEN tertiary_enrollment IS NOT NULL
                            THEN population END), 0), 1)            AS pop_weighted_enrollment_pct,
    ROUND(SUM(gdp_per_capita * population) / SUM(population), 0)    AS pop_weighted_gdp_pc_usd,
    ROUND(AVG(education_expenditure), 2)                             AS avg_edu_spend_pct_gdp,
    SUM(net_migration)                                               AS total_net_migration,
    ROUND(SUM(remittances) / 1e9, 2)                                 AS total_remittances_bn_usd
FROM country_year_metrics
WHERE is_africa = 1
  AND year IN (2010, 2015, 2020, 2024)
GROUP BY sub_region, year
ORDER BY sub_region, year;

-- -----------------------------------------------------------------------------
-- Q6. Education investment efficiency: spend per capita (USD) vs enrollment
--     outcome, NTILE quartiles and comparison to the USA's latest benchmark.
-- -----------------------------------------------------------------------------
WITH latest_spend AS (
    SELECT
        m.*,
        ROW_NUMBER() OVER (PARTITION BY m.iso3 ORDER BY m.year DESC) AS rn
    FROM country_year_metrics m
    WHERE m.education_spend_per_capita_usd IS NOT NULL
      AND m.tertiary_enrollment IS NOT NULL
)
SELECT
    iso3,
    country_name,
    year,
    ROUND(education_expenditure, 2)                         AS edu_spend_pct_gdp,
    ROUND(education_spend_per_capita_usd, 0)                AS edu_spend_pc_usd,
    ROUND(tertiary_enrollment, 1)                           AS enrollment_pct,
    ROUND(tertiary_enrollment / NULLIF(education_spend_per_capita_usd, 0) * 100, 2)
                                                            AS enrollment_pp_per_100usd,
    NTILE(4) OVER (ORDER BY education_spend_per_capita_usd DESC) AS spend_quartile,
    ROUND(100.0 * education_spend_per_capita_usd
          / (SELECT education_spend_per_capita_usd FROM latest_spend
             WHERE iso3 = 'USA' AND rn = 1), 2)             AS spend_pct_of_usa_latest
FROM latest_spend
WHERE rn = 1
ORDER BY edu_spend_pc_usd DESC;

-- -----------------------------------------------------------------------------
-- Q7. Migration flows: Africa (15) vs USA net migration over time, with each
--     side's running total and the USA's per-1,000 rate for context.
-- -----------------------------------------------------------------------------
WITH yearly AS (
    SELECT
        m.year,
        SUM(CASE WHEN m.is_africa = 1 THEN m.net_migration END)  AS africa15_net_migration,
        SUM(CASE WHEN m.iso3 = 'USA' THEN m.net_migration END)   AS usa_net_migration,
        MAX(CASE WHEN m.iso3 = 'USA' THEN m.net_migration_rate_per_1000 END) AS usa_rate_per_1000,
        SUM(CASE WHEN m.is_africa = 1 AND m.net_migration < 0 THEN 1 ELSE 0 END)
                                                                  AS africa_net_sending_countries
    FROM country_year_metrics m
    GROUP BY m.year
)
SELECT
    year,
    africa15_net_migration,
    SUM(africa15_net_migration) OVER (ORDER BY year)  AS africa15_cumulative,
    usa_net_migration,
    SUM(usa_net_migration) OVER (ORDER BY year)       AS usa_cumulative,
    ROUND(usa_rate_per_1000, 2)                       AS usa_rate_per_1000,
    africa_net_sending_countries
FROM yearly
WHERE year >= 2010
ORDER BY year;

-- -----------------------------------------------------------------------------
-- Q8. Income convergence: GDP per capita as % of USA, 2015 vs latest, with
--     DENSE_RANK movement and a FIRST_VALUE/LAST_VALUE trajectory check.
-- -----------------------------------------------------------------------------
WITH traj AS (
    SELECT
        m.iso3,
        m.country_name,
        m.year,
        m.gdp_pc_pct_of_usa,
        FIRST_VALUE(m.gdp_pc_pct_of_usa) OVER w AS pct_usa_2015,
        LAST_VALUE(m.gdp_pc_pct_of_usa)  OVER w AS pct_usa_latest,
        MAX(m.year) OVER (PARTITION BY m.iso3)  AS latest_year
    FROM country_year_metrics m
    WHERE m.is_africa = 1 AND m.year >= 2015 AND m.gdp_pc_pct_of_usa IS NOT NULL
    WINDOW w AS (PARTITION BY m.iso3 ORDER BY m.year
                 ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
),
summary AS (
    SELECT DISTINCT iso3, country_name, latest_year, pct_usa_2015, pct_usa_latest
    FROM traj
)
SELECT
    iso3,
    country_name,
    latest_year,
    ROUND(pct_usa_2015, 2)                               AS gdp_pc_pct_usa_2015,
    ROUND(pct_usa_latest, 2)                             AS gdp_pc_pct_usa_latest,
    ROUND(pct_usa_latest - pct_usa_2015, 2)              AS change_pp,
    DENSE_RANK() OVER (ORDER BY pct_usa_2015 DESC)       AS rank_2015,
    DENSE_RANK() OVER (ORDER BY pct_usa_latest DESC)     AS rank_latest,
    CASE WHEN pct_usa_latest > pct_usa_2015 THEN 'Converging'
         ELSE 'Diverging' END                            AS trajectory
FROM summary
ORDER BY change_pp DESC;
