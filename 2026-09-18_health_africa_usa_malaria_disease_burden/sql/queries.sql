-- =============================================================================
-- Analytical queries for the Malaria & Infectious Disease Burden project.
-- Target database: data/health_disease_burden.db (SQLite)
-- Run with:  sqlite3 data/health_disease_burden.db < sql/queries.sql
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Query 1: Top 10 African countries by average malaria incidence (2015-2023).
-- -----------------------------------------------------------------------------
SELECT
    mi.country_code,
    mi.country_name,
    ROUND(AVG(mi.incidence_per_1000), 2) AS avg_incidence_per_1000,
    COUNT(*)                             AS years_observed
FROM malaria_incidence AS mi
WHERE mi.year BETWEEN 2015 AND 2023
GROUP BY mi.country_code, mi.country_name
ORDER BY avg_incidence_per_1000 DESC
LIMIT 10;

-- -----------------------------------------------------------------------------
-- Query 2: Year-over-year malaria incidence trend for Nigeria and Kenya.
-- -----------------------------------------------------------------------------
SELECT
    country_code,
    country_name,
    year,
    incidence_per_1000,
    incidence_yoy_change
FROM malaria_incidence
WHERE country_code IN ('NGA', 'KEN')
ORDER BY country_code, year;

-- -----------------------------------------------------------------------------
-- Query 3: Countries with highest malaria burden but lowest health expenditure.
--          Joins malaria incidence to health expenditure on country + year.
-- -----------------------------------------------------------------------------
SELECT
    mi.country_code,
    mi.country_name,
    ROUND(AVG(mi.incidence_per_1000), 2)         AS avg_incidence,
    ROUND(AVG(hi.health_expenditure_pct_gdp), 2) AS avg_health_exp_pct_gdp
FROM malaria_incidence AS mi
JOIN health_indicators AS hi
    ON mi.country_code = hi.country_code
   AND mi.year = hi.year
WHERE hi.health_expenditure_pct_gdp IS NOT NULL
GROUP BY mi.country_code, mi.country_name
ORDER BY avg_incidence DESC, avg_health_exp_pct_gdp ASC
LIMIT 10;

-- -----------------------------------------------------------------------------
-- Query 4: Correlation proxy — rank countries by health expenditure and by
--          under-5 mortality (latest common year) to inspect their alignment.
-- -----------------------------------------------------------------------------
WITH latest AS (
    SELECT country_code, MAX(year) AS max_year
    FROM health_indicators
    WHERE health_expenditure_pct_gdp IS NOT NULL
      AND under5_mortality_per_1000 IS NOT NULL
    GROUP BY country_code
)
SELECT
    hi.country_code,
    hi.country_name,
    hi.year,
    hi.health_expenditure_pct_gdp,
    hi.under5_mortality_per_1000,
    RANK() OVER (ORDER BY hi.health_expenditure_pct_gdp DESC) AS rank_by_health_exp,
    RANK() OVER (ORDER BY hi.under5_mortality_per_1000 ASC)   AS rank_by_low_under5_mortality
FROM health_indicators AS hi
JOIN latest AS l
    ON hi.country_code = l.country_code
   AND hi.year = l.max_year
ORDER BY hi.health_expenditure_pct_gdp DESC;

-- -----------------------------------------------------------------------------
-- Query 5: Window function — rank countries by malaria deaths within each year.
-- -----------------------------------------------------------------------------
SELECT
    country_code,
    country_name,
    year,
    deaths_count,
    RANK() OVER (
        PARTITION BY year
        ORDER BY deaths_count DESC
    ) AS deaths_rank_in_year
FROM malaria_deaths
WHERE deaths_count IS NOT NULL
ORDER BY year DESC, deaths_rank_in_year ASC;

-- -----------------------------------------------------------------------------
-- Query 6: Life-expectancy gap — Africa average vs USA over time.
-- -----------------------------------------------------------------------------
WITH africa AS (
    SELECT year, ROUND(AVG(life_expectancy_years), 2) AS africa_life_exp
    FROM health_indicators
    WHERE region = 'Africa' AND life_expectancy_years IS NOT NULL
    GROUP BY year
),
usa AS (
    SELECT year, life_expectancy_years AS usa_life_exp
    FROM health_indicators
    WHERE region = 'USA' AND life_expectancy_years IS NOT NULL
)
SELECT
    a.year,
    a.africa_life_exp,
    u.usa_life_exp,
    ROUND(u.usa_life_exp - a.africa_life_exp, 2) AS life_exp_gap_years
FROM africa AS a
JOIN usa AS u ON a.year = u.year
ORDER BY a.year;

-- -----------------------------------------------------------------------------
-- Query 7: Countries showing a >20% reduction in malaria incidence 2015 -> 2022.
-- -----------------------------------------------------------------------------
WITH y2015 AS (
    SELECT country_code, incidence_per_1000 AS inc_2015
    FROM malaria_incidence WHERE year = 2015
),
y2022 AS (
    SELECT country_code, incidence_per_1000 AS inc_2022
    FROM malaria_incidence WHERE year = 2022
)
SELECT
    m.country_code,
    md.country_name,
    a.inc_2015,
    b.inc_2022,
    ROUND((b.inc_2022 - a.inc_2015) / a.inc_2015 * 100.0, 2) AS pct_change
FROM y2015 AS a
JOIN y2022 AS b ON a.country_code = b.country_code
JOIN malaria_incidence AS m ON m.country_code = a.country_code AND m.year = 2022
JOIN country_metadata AS md ON md.country_code = a.country_code
WHERE a.inc_2015 > 0
  AND (b.inc_2022 - a.inc_2015) / a.inc_2015 * 100.0 <= -20.0
GROUP BY m.country_code
ORDER BY pct_change ASC;

-- -----------------------------------------------------------------------------
-- Query 8: Health-expenditure trend — Africa average vs USA per year.
-- -----------------------------------------------------------------------------
SELECT
    year,
    ROUND(AVG(CASE WHEN region = 'Africa'
                   THEN health_expenditure_pct_gdp END), 2) AS africa_avg_health_exp,
    ROUND(AVG(CASE WHEN region = 'USA'
                   THEN health_expenditure_pct_gdp END), 2) AS usa_health_exp
FROM health_indicators
WHERE health_expenditure_pct_gdp IS NOT NULL
GROUP BY year
ORDER BY year;
