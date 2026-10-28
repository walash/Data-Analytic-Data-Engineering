-- ===========================================================================
-- Tuberculosis Burden in Africa & USA - analytical queries
-- Database : data/tb_burden.db (SQLite >= 3.25 for window functions)
-- Run with : sqlite3 -header -column data/tb_burden.db < sql/queries.sql
-- Tables   : dim_country, fact_tb_incidence, fact_tb_mortality,
--            fact_tb_treatment, fact_health_expenditure, tb_burden_summary,
--            v_latest_country_burden (view)
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- Q1. Africa vs USA headline comparison for the latest year with incidence
--     data. Africa figures are simple (unweighted) country averages.
-- ---------------------------------------------------------------------------
SELECT
    s.region,
    s.year,
    COUNT(DISTINCT s.country_code)           AS countries,
    ROUND(AVG(s.tb_incidence_per_100k), 1)   AS avg_incidence_per_100k,
    ROUND(MAX(s.tb_incidence_per_100k), 1)   AS max_incidence_per_100k,
    ROUND(AVG(s.tb_mortality_per_100k), 2)   AS avg_mortality_per_100k,
    ROUND(AVG(s.case_fatality_ratio_pct), 1) AS avg_case_fatality_pct
FROM tb_burden_summary s
WHERE s.year = (SELECT MAX(year) FROM fact_tb_incidence)
GROUP BY s.region, s.year
ORDER BY avg_incidence_per_100k DESC;


-- ---------------------------------------------------------------------------
-- Q2. Top 10 highest-burden African countries in the latest year, with a
--     multiple of the US rate (how many times higher than the USA).
-- ---------------------------------------------------------------------------
WITH latest AS (
    SELECT MAX(year) AS yr FROM fact_tb_incidence
),
usa AS (
    SELECT i.tb_incidence_per_100k AS usa_rate
    FROM fact_tb_incidence i, latest
    WHERE i.country_code = 'USA' AND i.year = latest.yr
)
SELECT
    c.country_name,
    c.sub_region,
    i.tb_incidence_per_100k,
    i.low_estimate,
    i.high_estimate,
    i.burden_category,
    ROUND(i.tb_incidence_per_100k / usa.usa_rate, 0) AS times_usa_rate
FROM fact_tb_incidence i
JOIN dim_country c ON c.country_code = i.country_code
CROSS JOIN latest
CROSS JOIN usa
WHERE i.year = latest.yr
  AND c.region = 'Africa'
ORDER BY i.tb_incidence_per_100k DESC
LIMIT 10;


-- ---------------------------------------------------------------------------
-- Q3. Incidence trend by African sub-region vs USA, every 5 years since 2000.
-- ---------------------------------------------------------------------------
SELECT
    c.sub_region,
    i.year,
    ROUND(AVG(i.tb_incidence_per_100k), 1) AS avg_incidence_per_100k,
    COUNT(*)                               AS countries_reporting
FROM fact_tb_incidence i
JOIN dim_country c ON c.country_code = i.country_code
WHERE i.year IN (2000, 2005, 2010, 2015, 2020, 2024)
GROUP BY c.sub_region, i.year
ORDER BY c.sub_region, i.year;


-- ---------------------------------------------------------------------------
-- Q4. Window function: rank countries within each African sub-region by
--     latest incidence, and show each country's share of the sub-region total.
-- ---------------------------------------------------------------------------
SELECT
    sub_region,
    country_name,
    tb_incidence_per_100k,
    RANK() OVER (PARTITION BY sub_region
                 ORDER BY tb_incidence_per_100k DESC)              AS rank_in_sub_region,
    ROUND(100.0 * tb_incidence_per_100k
          / SUM(tb_incidence_per_100k) OVER (PARTITION BY sub_region), 1)
                                                                    AS pct_of_sub_region_sum
FROM v_latest_country_burden
WHERE region = 'Africa'
ORDER BY sub_region, rank_in_sub_region;


-- ---------------------------------------------------------------------------
-- Q5. Window function (LAG): biggest reductions in incidence over the last
--     decade (2014 -> 2024). Positive pct_change_10y = improvement.
-- ---------------------------------------------------------------------------
WITH series AS (
    SELECT
        i.country_code,
        i.year,
        i.tb_incidence_per_100k,
        LAG(i.tb_incidence_per_100k, 10) OVER (PARTITION BY i.country_code
                                               ORDER BY i.year) AS incidence_10y_ago
    FROM fact_tb_incidence i
)
SELECT
    c.country_name,
    c.region,
    s.incidence_10y_ago                                AS incidence_2014,
    s.tb_incidence_per_100k                            AS incidence_2024,
    ROUND(100.0 * (s.incidence_10y_ago - s.tb_incidence_per_100k)
          / s.incidence_10y_ago, 1)                    AS pct_reduction_10y
FROM series s
JOIN dim_country c ON c.country_code = s.country_code
WHERE s.year = 2024
  AND s.incidence_10y_ago > 0
ORDER BY pct_reduction_10y DESC
LIMIT 15;


-- ---------------------------------------------------------------------------
-- Q6. Window function (moving average): 3-year rolling average of TB
--     mortality for the USA and Africa's five most populous TB-burdened
--     countries, smoothing year-to-year noise.
-- ---------------------------------------------------------------------------
SELECT
    c.country_name,
    m.year,
    m.tb_mortality_per_100k,
    ROUND(AVG(m.tb_mortality_per_100k) OVER (
              PARTITION BY m.country_code ORDER BY m.year
              ROWS BETWEEN 2 PRECEDING AND CURRENT ROW), 2) AS mortality_3yr_avg
FROM fact_tb_mortality m
JOIN dim_country c ON c.country_code = m.country_code
WHERE m.country_code IN ('USA', 'NGA', 'ZAF', 'COD', 'ETH', 'KEN')
ORDER BY c.country_name, m.year;


-- ---------------------------------------------------------------------------
-- Q7. Treatment success vs the WHO 90 % target: latest reported success rate
--     per country, the gap to target, and an overall Africa/USA tally.
-- ---------------------------------------------------------------------------
WITH latest_treatment AS (
    SELECT
        t.country_code,
        t.year,
        t.tb_treatment_success_pct,
        ROW_NUMBER() OVER (PARTITION BY t.country_code ORDER BY t.year DESC) AS rn
    FROM fact_tb_treatment t
)
SELECT
    c.region,
    COUNT(*)                                                    AS countries_reporting,
    SUM(CASE WHEN lt.tb_treatment_success_pct >= 90 THEN 1 ELSE 0 END) AS meeting_target,
    ROUND(AVG(lt.tb_treatment_success_pct), 1)                  AS avg_success_pct,
    ROUND(MIN(lt.tb_treatment_success_pct), 1)                  AS min_success_pct,
    ROUND(AVG(MAX(90 - lt.tb_treatment_success_pct, 0)), 1)     AS avg_gap_to_target
FROM latest_treatment lt
JOIN dim_country c ON c.country_code = lt.country_code
WHERE lt.rn = 1
GROUP BY c.region;


-- ---------------------------------------------------------------------------
-- Q8. Multi-table join: incidence, mortality, treatment success and health
--     spending side-by-side for the latest year in which all TB metrics exist
--     for each country. Highlights where high burden meets low spending.
-- ---------------------------------------------------------------------------
WITH complete_years AS (
    SELECT i.country_code, MAX(i.year) AS yr
    FROM fact_tb_incidence i
    JOIN fact_tb_mortality m ON m.country_code = i.country_code AND m.year = i.year
    JOIN fact_tb_treatment t ON t.country_code = i.country_code AND t.year = i.year
    GROUP BY i.country_code
)
SELECT
    c.country_name,
    c.region,
    cy.yr                                 AS year,
    i.tb_incidence_per_100k,
    m.tb_mortality_per_100k,
    t.tb_treatment_success_pct,
    ROUND(h.health_expenditure_pct_gdp, 2) AS health_exp_pct_gdp
FROM complete_years cy
JOIN dim_country c       ON c.country_code = cy.country_code
JOIN fact_tb_incidence i ON i.country_code = cy.country_code AND i.year = cy.yr
JOIN fact_tb_mortality m ON m.country_code = cy.country_code AND m.year = cy.yr
JOIN fact_tb_treatment t ON t.country_code = cy.country_code AND t.year = cy.yr
LEFT JOIN fact_health_expenditure h
       ON h.country_code = cy.country_code AND h.year = cy.yr
ORDER BY i.tb_incidence_per_100k DESC
LIMIT 15;


-- ---------------------------------------------------------------------------
-- Q9. Burden-category distribution of African countries over time, showing
--     how many countries moved out of the "Very High" (>=300/100k) band.
-- ---------------------------------------------------------------------------
SELECT
    i.year,
    SUM(CASE WHEN i.burden_category = 'Very High' THEN 1 ELSE 0 END) AS very_high,
    SUM(CASE WHEN i.burden_category = 'High'      THEN 1 ELSE 0 END) AS high,
    SUM(CASE WHEN i.burden_category = 'Moderate'  THEN 1 ELSE 0 END) AS moderate,
    SUM(CASE WHEN i.burden_category = 'Low'       THEN 1 ELSE 0 END) AS low
FROM fact_tb_incidence i
JOIN dim_country c ON c.country_code = i.country_code
WHERE c.region = 'Africa'
  AND i.year IN (2000, 2005, 2010, 2015, 2020, 2024)
GROUP BY i.year
ORDER BY i.year;


-- ---------------------------------------------------------------------------
-- Q10. Data quality: estimate uncertainty (high-low bound width relative to
--      point estimate) - widest relative uncertainty flags weak surveillance.
-- ---------------------------------------------------------------------------
SELECT
    country_name,
    region,
    year,
    tb_incidence_per_100k,
    uncertainty_range,
    ROUND(100.0 * uncertainty_range / tb_incidence_per_100k, 1) AS relative_uncertainty_pct,
    data_completeness
FROM v_latest_country_burden
WHERE tb_incidence_per_100k > 0
ORDER BY relative_uncertainty_pct DESC
LIMIT 10;
