-- =============================================================================
-- Brain Drain: Skilled Worker Emigration from Africa to the USA
-- Analytical queries against data/brain_drain.db (SQLite, built by src/loading/load.py)
--
-- Run all:   sqlite3 -header -column data/brain_drain.db < sql/queries.sql
-- Tables:    dim_country, fact_indicator, country_year_metrics,
--            brain_drain_index, pipeline_runs
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Q1. Brain Drain Pressure Index leaderboard
-- Which countries combine the strongest push factors (unemployment, low income,
-- net outflow, remittance dependence)? Score 0-100, rank 1 = highest pressure.
-- -----------------------------------------------------------------------------
SELECT brain_drain_rank                 AS rank,
       country_name,
       region,
       brain_drain_index,
       ROUND(unemployment, 2)           AS unemployment_pct,
       ROUND(gdp_per_capita, 0)         AS gdp_per_capita_usd,
       ROUND(net_migration_per_1000, 2) AS net_migration_per_1000,
       ROUND(remittances_pct_gdp, 2)    AS remittances_pct_gdp
FROM brain_drain_index
ORDER BY brain_drain_rank;


-- -----------------------------------------------------------------------------
-- Q2. Cumulative net migration over the last decade (2016-2025)
-- Negative totals = more people left than arrived (net emigration countries).
-- -----------------------------------------------------------------------------
SELECT c.country_name,
       c.region,
       SUM(m.net_migration)                               AS cumulative_net_migration,
       SUM(CASE WHEN m.net_migration < 0 THEN 1 ELSE 0 END) AS years_of_net_outflow,
       COUNT(m.net_migration)                             AS years_observed
FROM country_year_metrics m
JOIN dim_country c USING (country_code)
WHERE m.year BETWEEN 2016 AND 2025
GROUP BY c.country_name, c.region
ORDER BY cumulative_net_migration ASC;


-- -----------------------------------------------------------------------------
-- Q3. Remittance inflows: the "return" on the diaspora, latest year
-- Total USD received and dependence (% of GDP) - Nigeria and Egypt dominate
-- in absolute terms, smaller economies in relative terms.
-- -----------------------------------------------------------------------------
SELECT country_name,
       remittances_usd_year                       AS year,
       ROUND(remittances_usd / 1e9, 2)            AS remittances_usd_bn,
       ROUND(remittances_pct_gdp, 2)              AS remittances_pct_gdp,
       ROUND(100.0 * remittances_usd
             / SUM(remittances_usd) OVER (), 1)   AS share_of_panel_total_pct
FROM brain_drain_index
WHERE remittances_usd IS NOT NULL
ORDER BY remittances_usd DESC;


-- -----------------------------------------------------------------------------
-- Q4. Remittance growth: first vs. latest available year per country (CAGR)
-- -----------------------------------------------------------------------------
WITH bounds AS (
    SELECT country_code,
           MIN(year) AS first_year,
           MAX(year) AS last_year
    FROM country_year_metrics
    WHERE remittances_usd IS NOT NULL
    GROUP BY country_code
)
SELECT c.country_name,
       b.first_year,
       b.last_year,
       ROUND(f.remittances_usd / 1e9, 2) AS first_bn,
       ROUND(l.remittances_usd / 1e9, 2) AS last_bn,
       ROUND(100.0 * (l.remittances_usd - f.remittances_usd) / f.remittances_usd, 1)
                                         AS total_growth_pct,
       ROUND(100.0 * (EXP(LN(l.remittances_usd / f.remittances_usd)
             / NULLIF(b.last_year - b.first_year, 0)) - 1), 2) AS cagr_pct
FROM bounds b
JOIN country_year_metrics f ON f.country_code = b.country_code AND f.year = b.first_year
JOIN country_year_metrics l ON l.country_code = b.country_code AND l.year = b.last_year
JOIN dim_country c ON c.country_code = b.country_code
WHERE f.remittances_usd > 0
ORDER BY total_growth_pct DESC;


-- -----------------------------------------------------------------------------
-- Q5. Regional trend: net migration and remittances by African sub-region & year
-- -----------------------------------------------------------------------------
SELECT region,
       year,
       SUM(net_migration)                    AS net_migration,
       ROUND(SUM(remittances_usd) / 1e9, 2)  AS remittances_usd_bn,
       ROUND(AVG(unemployment), 2)           AS avg_unemployment_pct,
       ROUND(AVG(gdp_per_capita), 0)         AS avg_gdp_per_capita_usd
FROM country_year_metrics
WHERE year >= 2016
GROUP BY region, year
ORDER BY region, year;


-- -----------------------------------------------------------------------------
-- Q6. Push factors: high unemployment AND low income in the latest year
-- Countries above the panel-average unemployment rate or below the average
-- GDP per capita are flagged as high-push labour markets.
-- -----------------------------------------------------------------------------
WITH latest AS (
    SELECT country_code, MAX(year) AS year
    FROM country_year_metrics
    WHERE unemployment IS NOT NULL AND gdp_per_capita IS NOT NULL
    GROUP BY country_code
),
snap AS (
    SELECT m.country_name, m.year, m.unemployment, m.gdp_per_capita, m.net_migration
    FROM country_year_metrics m
    JOIN latest l USING (country_code, year)
)
SELECT country_name,
       year,
       ROUND(unemployment, 2)   AS unemployment_pct,
       ROUND(gdp_per_capita, 0) AS gdp_per_capita_usd,
       net_migration,
       CASE
           WHEN unemployment > (SELECT AVG(unemployment) FROM snap)
            AND gdp_per_capita < (SELECT AVG(gdp_per_capita) FROM snap) THEN 'High unemployment + low income'
           WHEN unemployment > (SELECT AVG(unemployment) FROM snap)   THEN 'High unemployment'
           WHEN gdp_per_capita < (SELECT AVG(gdp_per_capita) FROM snap) THEN 'Low income'
           ELSE 'Lower push'
       END AS push_profile
FROM snap
ORDER BY unemployment DESC;


-- -----------------------------------------------------------------------------
-- Q7. Year-over-year change in net migration with LAG() window function
-- Highlights years when outflows accelerated (e.g. post-COVID recovery).
-- -----------------------------------------------------------------------------
SELECT country_name,
       year,
       net_migration,
       LAG(net_migration) OVER w                    AS prev_year,
       net_migration - LAG(net_migration) OVER w    AS yoy_change,
       RANK() OVER (PARTITION BY year ORDER BY net_migration ASC) AS outflow_rank_in_year
FROM country_year_metrics
WHERE net_migration IS NOT NULL
WINDOW w AS (PARTITION BY country_code ORDER BY year)
ORDER BY country_name, year;


-- -----------------------------------------------------------------------------
-- Q8. Education pipeline: tertiary enrollment vs. net migration
-- Countries that train more graduates but cannot absorb them domestically
-- are the classic brain-drain profile.
-- -----------------------------------------------------------------------------
SELECT country_name,
       tertiary_enrollment_year                    AS enrollment_year,
       ROUND(tertiary_enrollment, 2)               AS tertiary_enrollment_gross_pct,
       ROUND(unemployment, 2)                      AS unemployment_pct,
       ROUND(net_migration_per_1000, 2)            AS net_migration_per_1000,
       CASE WHEN tertiary_enrollment >= 15 AND net_migration_per_1000 < 0
            THEN 'Educated & losing people' ELSE '-' END AS flag
FROM brain_drain_index
WHERE tertiary_enrollment IS NOT NULL
ORDER BY tertiary_enrollment DESC;


-- -----------------------------------------------------------------------------
-- Q9. Remittances per capita vs. GDP per capita (diaspora income multiplier)
-- How many US dollars per resident flow home from abroad each year?
-- Uses each country's latest year with both remittances and GDP reported.
-- -----------------------------------------------------------------------------
WITH latest AS (
    SELECT country_code, MAX(year) AS year
    FROM country_year_metrics
    WHERE remittances_per_capita_usd IS NOT NULL AND gdp_per_capita IS NOT NULL
    GROUP BY country_code
)
SELECT m.country_name,
       m.year,
       ROUND(m.remittances_per_capita_usd, 2)                           AS remit_per_capita_usd,
       ROUND(m.gdp_per_capita, 0)                                       AS gdp_per_capita_usd,
       ROUND(100.0 * m.remittances_per_capita_usd / m.gdp_per_capita, 2) AS remit_to_gdp_pc_pct
FROM country_year_metrics m
JOIN latest l USING (country_code, year)
ORDER BY remit_per_capita_usd DESC;


-- -----------------------------------------------------------------------------
-- Q10. Correlation check: unemployment vs. net migration rate (Pearson r)
-- Computed in pure SQL across all country-years with both values.
-- -----------------------------------------------------------------------------
WITH pairs AS (
    SELECT unemployment AS x, net_migration_per_1000 AS y
    FROM country_year_metrics
    WHERE unemployment IS NOT NULL AND net_migration_per_1000 IS NOT NULL
),
stats AS (
    SELECT COUNT(*) AS n, AVG(x) AS mx, AVG(y) AS my FROM pairs
)
SELECT s.n AS observations,
       ROUND(SUM((p.x - s.mx) * (p.y - s.my))
             / (SQRT(SUM((p.x - s.mx) * (p.x - s.mx)))
                * SQRT(SUM((p.y - s.my) * (p.y - s.my)))), 3) AS pearson_r_unemployment_vs_netmig
FROM pairs p CROSS JOIN stats s;


-- -----------------------------------------------------------------------------
-- Q11. International migrant stock trend (5-year intervals)
-- Stock of foreign-born residents - shows which countries are also destinations
-- (e.g. South Africa, Cote d'Ivoire) rather than pure source countries.
-- -----------------------------------------------------------------------------
SELECT c.country_name,
       f.year,
       f.value AS migrant_stock,
       ROUND(100.0 * (f.value - LAG(f.value) OVER (PARTITION BY f.country_code ORDER BY f.year))
             / LAG(f.value) OVER (PARTITION BY f.country_code ORDER BY f.year), 1)
             AS change_vs_prev_obs_pct
FROM fact_indicator f
JOIN dim_country c USING (country_code)
WHERE f.indicator = 'migrant_stock'
ORDER BY c.country_name, f.year;


-- -----------------------------------------------------------------------------
-- Q12. Data quality / coverage audit: observations per indicator
-- -----------------------------------------------------------------------------
SELECT indicator,
       indicator_code,
       COUNT(*)                     AS observations,
       COUNT(DISTINCT country_code) AS countries,
       MIN(year)                    AS first_year,
       MAX(year)                    AS last_year
FROM fact_indicator
GROUP BY indicator, indicator_code
ORDER BY indicator;
