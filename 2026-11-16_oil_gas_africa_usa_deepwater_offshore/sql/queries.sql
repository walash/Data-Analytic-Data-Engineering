-- =====================================================================
-- Deepwater Offshore Oil Production: Africa vs USA Gulf of Mexico
-- Analytical queries against data/oil_production.db (SQLite 3.25+,
-- window functions required).
--
-- Run all:   sqlite3 -header -column data/oil_production.db < sql/queries.sql
--
-- Tables (created by src/loading/load.py):
--   oil_production(country, iso_code, region, deepwater_basin, year,
--                  oil_production_twh, oil_production_kbd, oil_production_kbd_bbl,
--                  oil_prod_per_capita, prod_mwh_per_capita, population, gdp,
--                  yoy_change_twh, yoy_change_pct, cumulative_twh,
--                  share_of_region_pct, has_production, ...)
--   oil_rents(country, iso_code, region, year, oil_rents_pct_gdp, gdp,
--             population, oil_rents_usd, gdp_per_capita, has_rents)
--
-- Note: OWID production for Cameroon and Ghana is unavailable after 2016;
-- World Bank oil rents are unavailable for 2022-2023. Queries filter on
-- has_production / has_rents so gaps are never counted as zero.
-- =====================================================================


-- ---------------------------------------------------------------------
-- Q1. Top producers by year
-- Rank every country within each year by oil production (TWh) and keep
-- the top 3, showing the share of the year's total for the 8-country set.
-- ---------------------------------------------------------------------
WITH ranked AS (
    SELECT
        year,
        country,
        region,
        oil_production_twh,
        oil_production_kbd_bbl,
        RANK() OVER (PARTITION BY year ORDER BY oil_production_twh DESC) AS rank_in_year,
        ROUND(100.0 * oil_production_twh
              / SUM(oil_production_twh) OVER (PARTITION BY year), 2)      AS pct_of_year_total
    FROM oil_production
    WHERE has_production = 1
)
SELECT year, rank_in_year, country, region,
       ROUND(oil_production_twh, 1) AS production_twh,
       oil_production_kbd_bbl       AS production_kbd_bbl,
       pct_of_year_total
FROM ranked
WHERE rank_in_year <= 3
ORDER BY year, rank_in_year;


-- ---------------------------------------------------------------------
-- Q2. Africa vs USA production comparison by year
-- Uses only the five African producers with a complete 2010-2023 series
-- (NGA, AGO, GNQ, COG, GAB) so the Africa total is comparable year to
-- year, and reports the USA:Africa ratio.
-- ---------------------------------------------------------------------
WITH yearly AS (
    SELECT
        year,
        SUM(CASE WHEN region = 'Africa'
                  AND iso_code IN ('NGA','AGO','GNQ','COG','GAB')
                 THEN oil_production_twh END)                         AS africa_core5_twh,
        SUM(CASE WHEN region = 'USA' THEN oil_production_twh END)     AS usa_twh
    FROM oil_production
    WHERE has_production = 1
    GROUP BY year
)
SELECT
    year,
    ROUND(africa_core5_twh, 1)                    AS africa_core5_twh,
    ROUND(usa_twh, 1)                             AS usa_twh,
    ROUND(usa_twh / africa_core5_twh, 2)          AS usa_to_africa_ratio,
    ROUND(100.0 * africa_core5_twh
          / (africa_core5_twh + usa_twh), 2)      AS africa_share_pct
FROM yearly
ORDER BY year;


-- ---------------------------------------------------------------------
-- Q3. Oil rents trend by country
-- Oil rents (% of GDP) per country with the change versus the previous
-- year and versus 2010, plus implied rent value in USD billions.
-- ---------------------------------------------------------------------
SELECT
    country,
    year,
    ROUND(oil_rents_pct_gdp, 2)                                        AS rents_pct_gdp,
    ROUND(oil_rents_pct_gdp
          - LAG(oil_rents_pct_gdp) OVER (PARTITION BY iso_code ORDER BY year), 2)
                                                                       AS change_vs_prev_pp,
    ROUND(oil_rents_pct_gdp
          - FIRST_VALUE(oil_rents_pct_gdp) OVER (PARTITION BY iso_code ORDER BY year), 2)
                                                                       AS change_vs_2010_pp,
    ROUND(oil_rents_usd / 1e9, 2)                                      AS rents_usd_bn
FROM oil_rents
WHERE has_rents = 1
ORDER BY country, year;


-- ---------------------------------------------------------------------
-- Q4. Year-over-year production change
-- Absolute and percentage YoY change computed with LAG (recomputed here
-- rather than read from the precomputed column, as a cross-check), and a
-- label for the direction of change.
-- ---------------------------------------------------------------------
SELECT
    country,
    year,
    ROUND(oil_production_twh, 1)                                       AS production_twh,
    ROUND(oil_production_twh
          - LAG(oil_production_twh) OVER w, 1)                         AS yoy_change_twh,
    ROUND(100.0 * (oil_production_twh - LAG(oil_production_twh) OVER w)
          / LAG(oil_production_twh) OVER w, 2)                         AS yoy_change_pct,
    CASE
        WHEN LAG(oil_production_twh) OVER w IS NULL THEN 'n/a'
        WHEN oil_production_twh > LAG(oil_production_twh) OVER w THEN 'growth'
        WHEN oil_production_twh < LAG(oil_production_twh) OVER w THEN 'decline'
        ELSE 'flat'
    END                                                                AS direction
FROM oil_production
WHERE has_production = 1
WINDOW w AS (PARTITION BY iso_code ORDER BY year)
ORDER BY country, year;


-- ---------------------------------------------------------------------
-- Q5. Cumulative production 2010-2023
-- Running total per country plus the regional running total, showing how
-- quickly each producer and each region accumulated output.
-- ---------------------------------------------------------------------
SELECT
    region,
    country,
    year,
    ROUND(oil_production_twh, 1)                                       AS production_twh,
    ROUND(SUM(oil_production_twh) OVER (
              PARTITION BY iso_code ORDER BY year
              ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 1)    AS country_cumulative_twh,
    ROUND(SUM(oil_production_twh) OVER (
              PARTITION BY region ORDER BY year
              RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 1)   AS region_cumulative_twh
FROM oil_production
WHERE has_production = 1
ORDER BY region, country, year;


-- ---------------------------------------------------------------------
-- Q6. Production per capita ranking
-- Average and latest-available production per person (kWh/person, OWID
-- definition) and the rank of each country on the latest value.
-- ---------------------------------------------------------------------
WITH latest AS (
    SELECT iso_code, MAX(year) AS latest_year
    FROM oil_production
    WHERE has_production = 1
    GROUP BY iso_code
)
SELECT
    RANK() OVER (ORDER BY p.oil_prod_per_capita DESC)          AS per_capita_rank,
    p.country,
    p.region,
    l.latest_year,
    ROUND(p.oil_prod_per_capita, 0)                            AS latest_kwh_per_capita,
    ROUND(avg_tbl.avg_kwh_per_capita, 0)                       AS avg_kwh_per_capita_2010_on,
    p.population                                               AS latest_population
FROM oil_production p
JOIN latest l
  ON p.iso_code = l.iso_code AND p.year = l.latest_year
JOIN (
    SELECT iso_code, AVG(oil_prod_per_capita) AS avg_kwh_per_capita
    FROM oil_production
    WHERE has_production = 1
    GROUP BY iso_code
) avg_tbl
  ON avg_tbl.iso_code = p.iso_code
ORDER BY per_capita_rank;


-- ---------------------------------------------------------------------
-- Q7. Correlation between oil rents (% GDP) and GDP
-- Pearson correlation coefficient computed from aggregates per country:
--   r = (n*Sxy - Sx*Sy) / sqrt((n*Sxx - Sx^2) * (n*Syy - Sy^2))
-- x = oil rents % of GDP, y = GDP in USD billions. SQLite has no SQRT in
-- older builds, so r^2 is returned exactly and r via the math extension
-- when available (SQLite >= 3.35 compiled with math functions).
-- Positive r: GDP rises with oil dependence (commodity-driven economy).
-- ---------------------------------------------------------------------
WITH pairs AS (
    SELECT iso_code, country, region,
           oil_rents_pct_gdp AS x,
           gdp / 1e9         AS y
    FROM oil_rents
    WHERE has_rents = 1 AND gdp IS NOT NULL
),
agg AS (
    SELECT iso_code, country, region,
           COUNT(*) AS n,
           SUM(x)   AS sx,  SUM(y)   AS sy,
           SUM(x*x) AS sxx, SUM(y*y) AS syy, SUM(x*y) AS sxy
    FROM pairs
    GROUP BY iso_code, country, region
)
SELECT
    country,
    region,
    n AS observations,
    ROUND((n*sxy - sx*sy) * (n*sxy - sx*sy)
          / ((n*sxx - sx*sx) * (n*syy - sy*sy)), 4)            AS r_squared,
    CASE WHEN (n*sxy - sx*sy) >= 0 THEN 'positive' ELSE 'negative' END AS direction,
    ROUND(sx / n, 2)                                           AS avg_rents_pct_gdp,
    ROUND(sy / n, 1)                                           AS avg_gdp_usd_bn
FROM agg
WHERE n >= 3
ORDER BY r_squared DESC;


-- ---------------------------------------------------------------------
-- Q8. Deepwater-relevant countries ranked by peak production
-- Peak year and peak output for each producer, the latest available
-- value and the decline (or growth) from peak, ranked by peak size.
-- ---------------------------------------------------------------------
WITH peaks AS (
    SELECT iso_code, country, region, deepwater_basin, year, oil_production_twh,
           ROW_NUMBER() OVER (PARTITION BY iso_code
                              ORDER BY oil_production_twh DESC, year) AS rn_peak,
           ROW_NUMBER() OVER (PARTITION BY iso_code ORDER BY year DESC) AS rn_latest
    FROM oil_production
    WHERE has_production = 1
)
SELECT
    RANK() OVER (ORDER BY pk.oil_production_twh DESC)          AS peak_rank,
    pk.country,
    pk.region,
    pk.deepwater_basin,
    pk.year                                                    AS peak_year,
    ROUND(pk.oil_production_twh, 1)                            AS peak_twh,
    lt.year                                                    AS latest_year,
    ROUND(lt.oil_production_twh, 1)                            AS latest_twh,
    ROUND(100.0 * (lt.oil_production_twh - pk.oil_production_twh)
          / pk.oil_production_twh, 1)                          AS pct_vs_peak
FROM peaks pk
JOIN peaks lt
  ON lt.iso_code = pk.iso_code AND lt.rn_latest = 1
WHERE pk.rn_peak = 1
ORDER BY peak_rank;
