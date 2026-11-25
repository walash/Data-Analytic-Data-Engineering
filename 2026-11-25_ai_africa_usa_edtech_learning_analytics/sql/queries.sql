-- =====================================================================
-- AI-Powered EdTech & Learning Analytics: Africa and USA
-- Analytical queries against data/education_analytics.db (SQLite 3.25+)
--
-- Tables (created by src/loading/load.py):
--   raw_indicators    long format: country_id, country_name, region,
--                     indicator_id, indicator_key, indicator_name, year, value
--   edtech_readiness  one row per country-year with all five indicators,
--                     edtech_readiness_score and derived metrics
--   country_summary   one row per country with latest values and rank
--
-- Run:  sqlite3 data/education_analytics.db < sql/queries.sql
-- =====================================================================

.headers on
.mode column

-- ---------------------------------------------------------------------
-- Q1. Top countries by EdTech readiness score
-- Composite of internet access (40%), youth literacy (30%) and tertiary
-- enrollment (30%) using each country's latest available values.
-- readiness_components shows how many of the 3 inputs were reported.
-- ---------------------------------------------------------------------
SELECT readiness_rank,
       country_name,
       region,
       ROUND(edtech_readiness_score, 2)   AS readiness_score,
       readiness_components,
       ROUND(internet_users_pct, 1)       AS internet_pct,
       ROUND(youth_literacy_rate, 1)      AS youth_literacy_pct,
       ROUND(tertiary_enrollment_rate, 1) AS tertiary_enroll_pct
FROM country_summary
ORDER BY edtech_readiness_score DESC
LIMIT 10;

-- ---------------------------------------------------------------------
-- Q2. Year-over-year change in primary completion rates
-- LAG() compares each reported year with the previous reported year.
-- ---------------------------------------------------------------------
WITH pcr AS (
    SELECT country_name,
           year,
           value,
           LAG(year)  OVER (PARTITION BY country_id ORDER BY year) AS prev_year,
           LAG(value) OVER (PARTITION BY country_id ORDER BY year) AS prev_value
    FROM raw_indicators
    WHERE indicator_key = 'primary_completion_rate'
)
SELECT country_name,
       prev_year,
       year,
       ROUND(prev_value, 2)         AS prev_completion_pct,
       ROUND(value, 2)              AS completion_pct,
       ROUND(value - prev_value, 2) AS yoy_change_pp
FROM pcr
WHERE prev_value IS NOT NULL
ORDER BY country_name, year;

-- ---------------------------------------------------------------------
-- Q3. Africa vs USA comparison on all indicators
-- Averages the latest value of each indicator across the countries of
-- each region (Africa = 15 countries, USA = 1).
-- ---------------------------------------------------------------------
SELECT region,
       COUNT(*)                                      AS countries,
       ROUND(AVG(primary_completion_rate), 2)        AS avg_primary_completion,
       ROUND(AVG(education_expenditure_pct_gdp), 2)  AS avg_edu_spend_pct_gdp,
       ROUND(AVG(tertiary_enrollment_rate), 2)       AS avg_tertiary_enrollment,
       ROUND(AVG(internet_users_pct), 2)             AS avg_internet_users,
       ROUND(AVG(youth_literacy_rate), 2)            AS avg_youth_literacy,
       ROUND(AVG(edtech_readiness_score), 2)         AS avg_readiness_score
FROM country_summary
GROUP BY region
ORDER BY region;

-- ---------------------------------------------------------------------
-- Q4. Countries with the highest education expenditure growth
-- Change in government education spending (% of GDP) between the first
-- and last reported year for each country.
-- ---------------------------------------------------------------------
WITH bounds AS (
    SELECT country_id, MIN(year) AS first_year, MAX(year) AS last_year
    FROM raw_indicators
    WHERE indicator_key = 'education_expenditure_pct_gdp'
    GROUP BY country_id
)
SELECT r1.country_name,
       b.first_year,
       b.last_year,
       ROUND(r1.value, 2)            AS spend_first_pct_gdp,
       ROUND(r2.value, 2)            AS spend_last_pct_gdp,
       ROUND(r2.value - r1.value, 2) AS change_pp
FROM bounds b
JOIN raw_indicators r1
  ON r1.country_id = b.country_id AND r1.year = b.first_year
 AND r1.indicator_key = 'education_expenditure_pct_gdp'
JOIN raw_indicators r2
  ON r2.country_id = b.country_id AND r2.year = b.last_year
 AND r2.indicator_key = 'education_expenditure_pct_gdp'
WHERE b.last_year > b.first_year
ORDER BY change_pp DESC;

-- ---------------------------------------------------------------------
-- Q5. Correlation between internet access and tertiary enrollment
-- Pearson r computed from aggregates over every country-year where both
-- indicators were reported, overall and for Africa only.
-- ---------------------------------------------------------------------
WITH pairs AS (
    SELECT region,
           internet_users_pct       AS x,
           tertiary_enrollment_rate AS y
    FROM edtech_readiness
    WHERE internet_users_pct IS NOT NULL
      AND tertiary_enrollment_rate IS NOT NULL
),
scoped AS (
    SELECT 'All countries' AS scope, x, y FROM pairs
    UNION ALL
    SELECT 'Africa only', x, y FROM pairs WHERE region = 'Africa'
)
SELECT scope,
       COUNT(*) AS n_pairs,
       ROUND(
         (COUNT(*) * SUM(x * y) - SUM(x) * SUM(y)) /
         (SQRT(COUNT(*) * SUM(x * x) - SUM(x) * SUM(x)) *
          SQRT(COUNT(*) * SUM(y * y) - SUM(y) * SUM(y))), 3) AS pearson_r
FROM scoped
GROUP BY scope;

-- ---------------------------------------------------------------------
-- Q6. Window function: rank countries by each indicator within region
-- Uses each country's latest observation per indicator.
-- ---------------------------------------------------------------------
WITH latest AS (
    SELECT country_name, region, indicator_key, year, value,
           ROW_NUMBER() OVER (PARTITION BY country_id, indicator_key
                              ORDER BY year DESC) AS rn
    FROM raw_indicators
)
SELECT indicator_key,
       region,
       country_name,
       year,
       ROUND(value, 2) AS value,
       RANK() OVER (PARTITION BY indicator_key, region
                    ORDER BY value DESC) AS rank_in_region,
       ROUND(PERCENT_RANK() OVER (PARTITION BY indicator_key
                                  ORDER BY value), 2) AS overall_pct_rank
FROM latest
WHERE rn = 1
ORDER BY indicator_key, region, rank_in_region;

-- ---------------------------------------------------------------------
-- Q7. Countries with the biggest improvement in youth literacy
-- Compares earliest vs latest youth literacy reading (15-24 years).
-- ---------------------------------------------------------------------
WITH lit AS (
    SELECT country_id, country_name, year, value,
           FIRST_VALUE(value) OVER w AS first_value,
           FIRST_VALUE(year)  OVER w AS first_year,
           ROW_NUMBER() OVER (PARTITION BY country_id
                              ORDER BY year DESC) AS rn
    FROM raw_indicators
    WHERE indicator_key = 'youth_literacy_rate'
    WINDOW w AS (PARTITION BY country_id ORDER BY year)
)
SELECT country_name,
       first_year,
       year                          AS latest_year,
       ROUND(first_value, 2)         AS first_literacy_pct,
       ROUND(value, 2)               AS latest_literacy_pct,
       ROUND(value - first_value, 2) AS improvement_pp
FROM lit
WHERE rn = 1 AND year > first_year
ORDER BY improvement_pp DESC;

-- ---------------------------------------------------------------------
-- Q8. Education expenditure vs primary completion rate
-- Average spend and completion per country over 2015-2025, bucketed by
-- spend level, with completion points delivered per 1% of GDP spent.
-- ---------------------------------------------------------------------
WITH per_country AS (
    SELECT country_name,
           region,
           AVG(education_expenditure_pct_gdp) AS avg_spend,
           AVG(primary_completion_rate)       AS avg_completion
    FROM edtech_readiness
    GROUP BY country_id
    HAVING avg_spend IS NOT NULL AND avg_completion IS NOT NULL
)
SELECT country_name,
       region,
       ROUND(avg_spend, 2)                  AS avg_spend_pct_gdp,
       ROUND(avg_completion, 2)             AS avg_primary_completion,
       CASE WHEN avg_spend >= 5 THEN 'High (>=5%)'
            WHEN avg_spend >= 3.5 THEN 'Medium (3.5-5%)'
            ELSE 'Low (<3.5%)' END          AS spend_band,
       ROUND(avg_completion / avg_spend, 1) AS completion_per_gdp_pct
FROM per_country
ORDER BY avg_spend DESC;
