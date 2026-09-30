-- =============================================================================
-- Maternal & Child Health (Africa vs USA) - Analytical Queries
-- Database : data/maternal_child_health.db (SQLite >= 3.25 for window functions)
-- Tables   : maternal_child_health_metrics, country_metadata, yearly_summary
-- Run      : sqlite3 data/maternal_child_health.db < sql/queries.sql
-- =============================================================================

.headers on
.mode column


-- -----------------------------------------------------------------------------
-- Q1. Top countries by maternal mortality ratio (latest year with data)
-- Which countries carry the highest maternal death burden today, and how far
-- are they from the SDG 3.1 target of < 70 deaths per 100,000 live births?
-- -----------------------------------------------------------------------------
WITH latest AS (
    SELECT country_code, MAX(year) AS year
    FROM maternal_child_health_metrics
    WHERE maternal_mortality_ratio IS NOT NULL
    GROUP BY country_code
)
SELECT
    m.country_name,
    m.region,
    m.year,
    m.maternal_mortality_ratio                         AS mmr,
    ROUND(m.maternal_mortality_ratio / 70.0, 1)        AS times_sdg_target,
    CASE WHEN m.meets_sdg_mmr = 1 THEN 'Yes' ELSE 'No' END AS meets_sdg_3_1
FROM maternal_child_health_metrics m
JOIN latest l USING (country_code, year)
ORDER BY mmr DESC
LIMIT 10;


-- -----------------------------------------------------------------------------
-- Q2. Trend analysis: change in MMR, U5MR and NMR between first and last
-- observed year per country (absolute and percentage change).
-- -----------------------------------------------------------------------------
WITH bounds AS (
    SELECT country_code,
           MIN(year) FILTER (WHERE maternal_mortality_ratio IS NOT NULL) AS first_mmr_year,
           MAX(year) FILTER (WHERE maternal_mortality_ratio IS NOT NULL) AS last_mmr_year,
           MIN(year) FILTER (WHERE under5_mortality_rate IS NOT NULL)    AS first_u5_year,
           MAX(year) FILTER (WHERE under5_mortality_rate IS NOT NULL)    AS last_u5_year
    FROM maternal_child_health_metrics
    GROUP BY country_code
)
SELECT
    c.country_name,
    c.region,
    b.first_mmr_year || '-' || b.last_mmr_year                           AS mmr_period,
    f.maternal_mortality_ratio                                           AS mmr_start,
    l.maternal_mortality_ratio                                           AS mmr_end,
    ROUND(100.0 * (l.maternal_mortality_ratio - f.maternal_mortality_ratio)
          / f.maternal_mortality_ratio, 1)                               AS mmr_change_pct,
    b.first_u5_year || '-' || b.last_u5_year                             AS u5_period,
    fu.under5_mortality_rate                                             AS u5mr_start,
    lu.under5_mortality_rate                                             AS u5mr_end,
    ROUND(100.0 * (lu.under5_mortality_rate - fu.under5_mortality_rate)
          / fu.under5_mortality_rate, 1)                                 AS u5mr_change_pct
FROM bounds b
JOIN country_metadata c USING (country_code)
JOIN maternal_child_health_metrics f  ON f.country_code  = b.country_code AND f.year  = b.first_mmr_year
JOIN maternal_child_health_metrics l  ON l.country_code  = b.country_code AND l.year  = b.last_mmr_year
JOIN maternal_child_health_metrics fu ON fu.country_code = b.country_code AND fu.year = b.first_u5_year
JOIN maternal_child_health_metrics lu ON lu.country_code = b.country_code AND lu.year = b.last_u5_year
ORDER BY mmr_change_pct ASC;


-- -----------------------------------------------------------------------------
-- Q3. Africa vs USA comparison by year, with the Africa/USA ratio ("gap")
-- for maternal and under-5 mortality and the spending gap.
-- -----------------------------------------------------------------------------
SELECT
    a.year,
    a.avg_maternal_mortality_ratio                                         AS africa_avg_mmr,
    u.avg_maternal_mortality_ratio                                         AS usa_mmr,
    ROUND(a.avg_maternal_mortality_ratio / u.avg_maternal_mortality_ratio, 1) AS mmr_gap_x,
    a.avg_under5_mortality_rate                                            AS africa_avg_u5mr,
    u.avg_under5_mortality_rate                                            AS usa_u5mr,
    ROUND(a.avg_under5_mortality_rate / u.avg_under5_mortality_rate, 1)    AS u5mr_gap_x,
    ROUND(a.avg_health_expenditure_per_capita_usd, 0)                      AS africa_avg_spend_usd,
    ROUND(u.avg_health_expenditure_per_capita_usd, 0)                      AS usa_spend_usd,
    ROUND(u.avg_health_expenditure_per_capita_usd
          / a.avg_health_expenditure_per_capita_usd, 0)                    AS spend_gap_x
FROM yearly_summary a
JOIN yearly_summary u ON u.year = a.year AND u.region = 'USA'
WHERE a.region = 'Africa'
  AND (a.avg_maternal_mortality_ratio IS NOT NULL OR a.avg_under5_mortality_rate IS NOT NULL)
ORDER BY a.year;


-- -----------------------------------------------------------------------------
-- Q4. Correlation analysis (Pearson r computed in SQL) between indicators
-- across all African country-years. SQLite has no CORR(), so we use
-- r = (n*Sxy - Sx*Sy) / sqrt((n*Sxx - Sx^2) * (n*Syy - Sy^2)).
-- -----------------------------------------------------------------------------
WITH pairs AS (
    SELECT 'fertility_rate vs under5_mortality_rate' AS pair,
           fertility_rate AS x, under5_mortality_rate AS y
    FROM maternal_child_health_metrics WHERE region = 'Africa'
    UNION ALL
    SELECT 'skilled_birth_attendance_pct vs maternal_mortality_ratio',
           skilled_birth_attendance_pct, maternal_mortality_ratio
    FROM maternal_child_health_metrics WHERE region = 'Africa'
    UNION ALL
    SELECT 'neonatal_mortality_rate vs under5_mortality_rate',
           neonatal_mortality_rate, under5_mortality_rate
    FROM maternal_child_health_metrics WHERE region = 'Africa'
    UNION ALL
    SELECT 'health_expenditure_per_capita_usd vs neonatal_mortality_rate',
           health_expenditure_per_capita_usd, neonatal_mortality_rate
    FROM maternal_child_health_metrics WHERE region = 'Africa'
),
stats AS (
    SELECT pair,
           COUNT(*) AS n, SUM(x) AS sx, SUM(y) AS sy,
           SUM(x * x) AS sxx, SUM(y * y) AS syy, SUM(x * y) AS sxy
    FROM pairs
    WHERE x IS NOT NULL AND y IS NOT NULL
    GROUP BY pair
)
SELECT
    pair,
    n AS observations,
    ROUND((n * sxy - sx * sy)
          / (SQRT(n * sxx - sx * sx) * SQRT(n * syy - sy * sy)), 3) AS pearson_r
FROM stats
ORDER BY ABS(pearson_r) DESC;


-- -----------------------------------------------------------------------------
-- Q5. Immunization impact: bucket African country-years by DPT coverage and
-- compare average under-5 and neonatal mortality in each coverage band.
-- -----------------------------------------------------------------------------
SELECT
    CASE
        WHEN immunization_dpt_pct >= 90 THEN '1) >= 90% (WHO target)'
        WHEN immunization_dpt_pct >= 80 THEN '2) 80-89%'
        WHEN immunization_dpt_pct >= 70 THEN '3) 70-79%'
        ELSE                                 '4) < 70%'
    END                                         AS dpt_coverage_band,
    COUNT(*)                                    AS country_years,
    COUNT(DISTINCT country_code)                AS countries,
    ROUND(AVG(immunization_dpt_pct), 1)         AS avg_dpt_pct,
    ROUND(AVG(under5_mortality_rate), 1)        AS avg_u5mr,
    ROUND(AVG(neonatal_mortality_rate), 1)      AS avg_nmr
FROM maternal_child_health_metrics
WHERE region = 'Africa'
  AND immunization_dpt_pct IS NOT NULL
  AND under5_mortality_rate IS NOT NULL
GROUP BY dpt_coverage_band
ORDER BY dpt_coverage_band;


-- -----------------------------------------------------------------------------
-- Q6. Health expenditure vs outcomes: average spending and outcomes per country
-- over the study period, with a "deaths averted per dollar" style efficiency
-- proxy (lower MMR per $ spent = more efficient).
-- -----------------------------------------------------------------------------
SELECT
    country_name,
    region,
    ROUND(AVG(health_expenditure_per_capita_usd), 0)  AS avg_spend_usd,
    ROUND(AVG(maternal_mortality_ratio), 0)           AS avg_mmr,
    ROUND(AVG(under5_mortality_rate), 1)              AS avg_u5mr,
    ROUND(AVG(immunization_dpt_pct), 1)               AS avg_dpt_pct,
    CASE
        WHEN AVG(health_expenditure_per_capita_usd) < 50   THEN 'Low (<$50)'
        WHEN AVG(health_expenditure_per_capita_usd) < 150  THEN 'Medium ($50-149)'
        WHEN AVG(health_expenditure_per_capita_usd) < 1000 THEN 'High ($150-999)'
        ELSE 'Very high (>= $1000)'
    END                                               AS spending_tier
FROM maternal_child_health_metrics
GROUP BY country_code, country_name, region
ORDER BY avg_spend_usd DESC;


-- -----------------------------------------------------------------------------
-- Q7. Window functions: rank countries each year by under-5 mortality
-- (1 = best/lowest), show the rank movement vs the previous year and each
-- country's 3-year moving average.
-- -----------------------------------------------------------------------------
WITH ranked AS (
    SELECT
        country_name,
        region,
        year,
        under5_mortality_rate,
        RANK() OVER (PARTITION BY year ORDER BY under5_mortality_rate ASC)   AS u5_rank,
        ROUND(AVG(under5_mortality_rate) OVER (
            PARTITION BY country_code ORDER BY year
            ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2)                    AS u5mr_3yr_moving_avg,
        NTILE(4) OVER (PARTITION BY year ORDER BY under5_mortality_rate ASC) AS quartile
    FROM maternal_child_health_metrics
    WHERE under5_mortality_rate IS NOT NULL
)
SELECT
    country_name,
    region,
    year,
    under5_mortality_rate,
    u5_rank,
    LAG(u5_rank) OVER (PARTITION BY country_name ORDER BY year) - u5_rank AS rank_improvement,
    u5mr_3yr_moving_avg,
    quartile
FROM ranked
WHERE year >= (SELECT MAX(year) - 2 FROM maternal_child_health_metrics
               WHERE under5_mortality_rate IS NOT NULL)
ORDER BY year DESC, u5_rank ASC;


-- -----------------------------------------------------------------------------
-- Q8. Year-over-year changes in maternal mortality using LAG(), highlighting
-- the COVID-19 period (2020-2021) and flagging reversals (MMR increases).
-- -----------------------------------------------------------------------------
WITH yoy AS (
    SELECT
        country_name,
        region,
        year,
        maternal_mortality_ratio AS mmr,
        LAG(maternal_mortality_ratio) OVER (PARTITION BY country_code ORDER BY year) AS prev_mmr
    FROM maternal_child_health_metrics
    WHERE maternal_mortality_ratio IS NOT NULL
)
SELECT
    country_name,
    region,
    year,
    prev_mmr,
    mmr,
    mmr - prev_mmr                                    AS abs_change,
    ROUND(100.0 * (mmr - prev_mmr) / prev_mmr, 1)     AS yoy_change_pct,
    CASE WHEN mmr > prev_mmr THEN 'REVERSAL' ELSE 'improving/flat' END AS direction,
    CASE WHEN year IN (2020, 2021) THEN 'COVID-19 period' ELSE '' END  AS period_note
FROM yoy
WHERE prev_mmr IS NOT NULL
  AND year IN (2020, 2021, 2022)
ORDER BY yoy_change_pct DESC;
