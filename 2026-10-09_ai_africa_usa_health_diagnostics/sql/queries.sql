-- =============================================================================
-- AI-Powered Healthcare Diagnostics: Bridging the Africa-USA Health Gap
-- Analytical queries for data/health_diagnostics.db (SQLite >= 3.25)
--
-- Run all:   sqlite3 data/health_diagnostics.db < sql/queries.sql
-- Tables:    health_indicators, country_summary, yearly_trends
-- Note:      sparse indicators (physicians, hospital beds) are forward-filled
--            within each country by src/transformation/transform.py.
-- =============================================================================

.headers on
.mode column

-- -----------------------------------------------------------------------------
-- Q1: Average health expenditure per capita by region (Africa vs USA)
--     Shows the absolute and relative spending gap for every year.
-- -----------------------------------------------------------------------------
SELECT
    year,
    ROUND(AVG(CASE WHEN africa_flag = 1 THEN health_expenditure_pc END), 2) AS africa_avg_usd,
    ROUND(AVG(CASE WHEN africa_flag = 0 THEN health_expenditure_pc END), 2) AS usa_usd,
    ROUND(
        AVG(CASE WHEN africa_flag = 0 THEN health_expenditure_pc END)
        / AVG(CASE WHEN africa_flag = 1 THEN health_expenditure_pc END), 1
    ) AS usa_to_africa_ratio
FROM health_indicators
WHERE health_expenditure_pc IS NOT NULL
GROUP BY year
ORDER BY year;

-- -----------------------------------------------------------------------------
-- Q2: Countries ranked by healthcare access score (latest common year)
--     RANK() overall plus a rank among African countries only.
-- -----------------------------------------------------------------------------
WITH latest AS (
    SELECT MAX(year) AS yr
    FROM health_indicators
    WHERE health_expenditure_pc IS NOT NULL
)
SELECT
    h.country_name,
    h.year,
    h.healthcare_access_score,
    RANK() OVER (ORDER BY h.healthcare_access_score DESC) AS overall_rank,
    CASE WHEN h.africa_flag = 1
         THEN RANK() OVER (PARTITION BY h.africa_flag ORDER BY h.healthcare_access_score DESC)
    END AS africa_rank
FROM health_indicators h
JOIN latest l ON h.year = l.yr
ORDER BY overall_rank;

-- -----------------------------------------------------------------------------
-- Q3: Year-over-year change in life expectancy per country (LAG)
-- -----------------------------------------------------------------------------
SELECT
    country_name,
    year,
    ROUND(life_expectancy, 2) AS life_expectancy,
    ROUND(life_expectancy - LAG(life_expectancy) OVER (
        PARTITION BY country_code ORDER BY year
    ), 3) AS yoy_change_years
FROM health_indicators
WHERE life_expectancy IS NOT NULL
ORDER BY country_name, year;

-- -----------------------------------------------------------------------------
-- Q4: Correlation proxy - countries with LOW physician density but HIGH
--     under-5 mortality (both relative to the African median-ish average),
--     i.e. where AI-assisted diagnostics could add the most capacity.
-- -----------------------------------------------------------------------------
WITH latest AS (
    SELECT h.*
    FROM health_indicators h
    WHERE h.year = (
        SELECT MAX(year) FROM health_indicators
        WHERE physicians_per_1000 IS NOT NULL AND under5_mortality IS NOT NULL
    )
),
bench AS (
    SELECT AVG(physicians_per_1000) AS avg_phys, AVG(under5_mortality) AS avg_u5
    FROM latest
    WHERE africa_flag = 1
)
SELECT
    l.country_name,
    l.year,
    l.physicians_per_1000,
    l.under5_mortality,
    ROUND(b.avg_phys, 3) AS africa_avg_physicians,
    ROUND(b.avg_u5, 1)   AS africa_avg_under5_mortality,
    ROUND(l.under5_mortality / NULLIF(l.physicians_per_1000, 0), 1) AS deaths_per_physician_index
FROM latest l
CROSS JOIN bench b
WHERE l.physicians_per_1000 < b.avg_phys
  AND l.under5_mortality > b.avg_u5 * 0.9
ORDER BY deaths_per_physician_index DESC;

-- -----------------------------------------------------------------------------
-- Q5: Top 5 African countries by improvement in under-5 mortality
--     (first vs latest observed year).
-- -----------------------------------------------------------------------------
WITH bounds AS (
    SELECT
        country_code,
        country_name,
        FIRST_VALUE(under5_mortality) OVER w AS first_u5,
        LAST_VALUE(under5_mortality)  OVER w AS latest_u5,
        MIN(year) OVER (PARTITION BY country_code) AS first_year,
        MAX(year) OVER (PARTITION BY country_code) AS latest_year
    FROM health_indicators
    WHERE africa_flag = 1 AND under5_mortality IS NOT NULL
    WINDOW w AS (PARTITION BY country_code ORDER BY year
                 ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
)
SELECT DISTINCT
    country_name,
    first_year,
    latest_year,
    first_u5,
    latest_u5,
    ROUND(first_u5 - latest_u5, 1)                  AS deaths_averted_per_1000,
    ROUND(100.0 * (first_u5 - latest_u5) / first_u5, 1) AS pct_improvement
FROM bounds
ORDER BY deaths_averted_per_1000 DESC
LIMIT 5;

-- -----------------------------------------------------------------------------
-- Q6: Healthcare gap ratio - USA vs African average for each indicator
--     (latest year in which every indicator has USA and Africa values).
--     For mortality the ratio is Africa / USA (higher = worse for Africa).
-- -----------------------------------------------------------------------------
WITH yr AS (
    SELECT MAX(year) AS y FROM health_indicators
    WHERE africa_flag = 0
      AND health_expenditure_pc IS NOT NULL
      AND physicians_per_1000 IS NOT NULL
      AND hospital_beds_per_1000 IS NOT NULL
),
regional AS (
    SELECT
        africa_flag,
        AVG(health_expenditure_pc)  AS exp_pc,
        AVG(physicians_per_1000)    AS phys,
        AVG(hospital_beds_per_1000) AS beds,
        AVG(under5_mortality)       AS u5,
        AVG(life_expectancy)        AS le
    FROM health_indicators, yr
    WHERE year = yr.y
    GROUP BY africa_flag
)
SELECT
    (SELECT y FROM yr)                       AS year,
    ROUND(us.exp_pc / af.exp_pc, 1)          AS health_expenditure_gap_x,
    ROUND(us.phys / af.phys, 1)              AS physicians_gap_x,
    ROUND(us.beds / af.beds, 1)              AS hospital_beds_gap_x,
    ROUND(af.u5 / us.u5, 1)                  AS under5_mortality_gap_x,
    ROUND(us.le - af.le, 1)                  AS life_expectancy_gap_years
FROM regional us
JOIN regional af ON us.africa_flag = 0 AND af.africa_flag = 1;

-- -----------------------------------------------------------------------------
-- Q7: Countries where health expenditure grew but under-5 mortality did NOT
--     improve year-over-year (spending not translating into outcomes).
-- -----------------------------------------------------------------------------
WITH deltas AS (
    SELECT
        country_name,
        year,
        health_expenditure_pc,
        under5_mortality,
        health_expenditure_pc - LAG(health_expenditure_pc) OVER (
            PARTITION BY country_code ORDER BY year) AS exp_change,
        under5_mortality - LAG(under5_mortality) OVER (
            PARTITION BY country_code ORDER BY year) AS u5_change
    FROM health_indicators
)
SELECT
    country_name,
    year,
    ROUND(exp_change, 2) AS expenditure_change_usd,
    ROUND(u5_change, 2)  AS under5_mortality_change
FROM deltas
WHERE exp_change > 0
  AND u5_change >= 0
ORDER BY country_name, year;

-- -----------------------------------------------------------------------------
-- Q8: Composite AI readiness score (most recent year per country)
--     Combines access score (infrastructure), spending headroom, outcome need
--     and life expectancy into a 0-100 score; higher = better positioned to
--     deploy AI diagnostics today. "Need" is reported separately so policy
--     makers can target high-need, moderate-readiness markets.
-- -----------------------------------------------------------------------------
WITH latest_year AS (
    SELECT country_code, MAX(year) AS yr
    FROM health_indicators
    WHERE health_expenditure_pc IS NOT NULL
    GROUP BY country_code
),
latest AS (
    SELECT h.*
    FROM health_indicators h
    JOIN latest_year ly ON h.country_code = ly.country_code AND h.year = ly.yr
),
bounds AS (
    SELECT
        MIN(life_expectancy) AS le_min, MAX(life_expectancy) AS le_max,
        MIN(under5_mortality) AS u5_min, MAX(under5_mortality) AS u5_max
    FROM latest
)
SELECT
    l.country_name,
    l.year,
    l.healthcare_access_score,
    ROUND(100.0 * (l.life_expectancy - b.le_min) / (b.le_max - b.le_min), 1) AS life_expectancy_index,
    ROUND(100.0 * (l.under5_mortality - b.u5_min) / (b.u5_max - b.u5_min), 1) AS diagnostic_need_index,
    ROUND(
        0.6 * l.healthcare_access_score
        + 0.4 * 100.0 * (l.life_expectancy - b.le_min) / (b.le_max - b.le_min), 1
    ) AS ai_readiness_score,
    cs.under5_mortality_change AS u5_trend_since_first_year
FROM latest l
CROSS JOIN bounds b
JOIN country_summary cs ON cs.country_code = l.country_code
ORDER BY ai_readiness_score DESC;
