-- =============================================================================
-- Analytical queries for the AI Adoption & Digital Infrastructure database
-- (data/digital_infrastructure.db). Run with:
--     sqlite3 data/digital_infrastructure.db < sql/queries.sql
-- Tables: digital_metrics (country-year panel), country_summary (latest year).
-- =============================================================================


-- 1. Top 10 African countries by internet penetration (latest year per country).
--    country_summary already holds the latest year for each country.
SELECT country_name,
       region,
       year,
       ROUND(internet_users_pct, 1) AS internet_pct
FROM country_summary
WHERE region <> 'USA'
ORDER BY internet_users_pct DESC
LIMIT 10;


-- 2. Internet penetration trend (2015-2023) for the top 5 African countries
--    (by latest internet penetration) alongside the USA benchmark.
WITH top5 AS (
    SELECT country_code
    FROM country_summary
    WHERE region <> 'USA'
    ORDER BY internet_users_pct DESC
    LIMIT 5
)
SELECT m.country_name,
       m.year,
       ROUND(m.internet_users_pct, 1) AS internet_pct
FROM digital_metrics AS m
WHERE m.year BETWEEN 2015 AND 2023
  AND (m.country_code IN (SELECT country_code FROM top5)
       OR m.country_code = 'USA')
  AND m.internet_users_pct IS NOT NULL
ORDER BY m.country_name, m.year;


-- 3. Digital readiness score ranking (latest year) using a window function.
--    RANK() orders every country globally; a partitioned rank orders within
--    each region so we can see the regional leaders too.
SELECT country_name,
       region,
       digital_readiness_score,
       RANK() OVER (ORDER BY digital_readiness_score DESC) AS global_rank,
       RANK() OVER (
           PARTITION BY region
           ORDER BY digital_readiness_score DESC
       ) AS region_rank
FROM country_summary
WHERE digital_readiness_score IS NOT NULL
ORDER BY global_rank;


-- 4. Mobile-vs-broadband adoption by region (latest year averages).
--    Shows how strongly each region leans on mobile vs fixed broadband.
SELECT region,
       COUNT(*)                                    AS countries,
       ROUND(AVG(mobile_subscriptions_per100), 1)  AS avg_mobile_per100,
       ROUND(AVG(fixed_broadband_per100), 2)       AS avg_broadband_per100,
       ROUND(AVG(mobile_subscriptions_per100)
             / NULLIF(AVG(fixed_broadband_per100), 0), 1) AS mobile_to_broadband_ratio
FROM country_summary
GROUP BY region
ORDER BY mobile_to_broadband_ratio DESC;


-- 5. Countries with the fastest internet growth, year over year, using LAG().
--    Computes the percentage-point gain vs the previous available year and
--    returns the single largest annual jump per country (top 15 overall).
WITH yoy AS (
    SELECT country_name,
           region,
           year,
           internet_users_pct,
           internet_users_pct
               - LAG(internet_users_pct) OVER (
                     PARTITION BY country_code ORDER BY year
                 ) AS pp_gain
    FROM digital_metrics
    WHERE internet_users_pct IS NOT NULL
)
SELECT country_name,
       region,
       year,
       ROUND(internet_users_pct, 1) AS internet_pct,
       ROUND(pp_gain, 1)            AS yoy_gain_pp
FROM yoy
WHERE pp_gain IS NOT NULL
ORDER BY pp_gain DESC
LIMIT 15;


-- 6. High-tech exports leaders: top African countries vs the USA benchmark
--    (latest year, only countries reporting a high-tech export figure).
SELECT country_name,
       region,
       year,
       ROUND(hightech_exports_pct, 2) AS hightech_exports_pct
FROM country_summary
WHERE hightech_exports_pct IS NOT NULL
ORDER BY hightech_exports_pct DESC
LIMIT 11;


-- 7. R&D expenditure vs internet penetration (latest year) for countries that
--    report both, ordered by R&D intensity. Useful for eyeballing correlation.
SELECT country_name,
       region,
       ROUND(rnd_expenditure_pct, 2) AS rnd_pct_gdp,
       ROUND(internet_users_pct, 1)  AS internet_pct,
       ROUND(digital_readiness_score, 1) AS readiness
FROM country_summary
WHERE rnd_expenditure_pct IS NOT NULL
  AND internet_users_pct IS NOT NULL
ORDER BY rnd_expenditure_pct DESC;


-- 8. Regional averages comparison using GROUP BY + HAVING.
--    Only keeps regions with at least three reporting countries so the averages
--    are meaningful, then ranks them by average digital readiness.
SELECT region,
       COUNT(*)                                   AS countries,
       ROUND(AVG(internet_users_pct), 1)          AS avg_internet_pct,
       ROUND(AVG(fixed_broadband_per100), 2)      AS avg_broadband_per100,
       ROUND(AVG(digital_readiness_score), 1)     AS avg_readiness
FROM country_summary
GROUP BY region
HAVING COUNT(*) >= 3
ORDER BY avg_readiness DESC;
