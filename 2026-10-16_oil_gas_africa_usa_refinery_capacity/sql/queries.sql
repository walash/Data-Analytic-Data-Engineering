-- =============================================================================
-- Oil & Gas Refinery Capacity & Fuel Access in Africa and USA
-- Analytical queries against data/oil_gas_refinery.db (SQLite >= 3.25 for
-- window functions).
--
-- Run all:     sqlite3 -header -column data/oil_gas_refinery.db < sql/queries.sql
-- Tables:      dim_country, dim_indicator, fact_energy_indicators,
--              country_latest_snapshot, data_quality_report, pipeline_runs
-- View:        v_regional_yearly
-- =============================================================================


-- -----------------------------------------------------------------------------
-- Q1. The "crude-out, products-in" paradox: latest fuel-trade position.
-- Countries whose merchandise exports are dominated by fuel yet still spend a
-- large share of their import bill on fuel are signalling a domestic refining
-- capacity shortfall. Ranked by the import share of fuel.
-- -----------------------------------------------------------------------------
SELECT  c.country,
        c.subregion,
        s.fuel_exports_pct_merchandise_year            AS trade_year,
        ROUND(s.fuel_exports_pct_merchandise, 1)        AS fuel_exports_pct,
        ROUND(s.fuel_imports_pct_merchandise, 1)        AS fuel_imports_pct,
        ROUND(s.fuel_exports_pct_merchandise
              - s.fuel_imports_pct_merchandise, 1)      AS net_fuel_trade_pp,
        CASE
            WHEN s.fuel_exports_pct_merchandise >= 50
             AND s.fuel_imports_pct_merchandise >= 15 THEN 'Refining gap'
            WHEN s.fuel_exports_pct_merchandise >= 50  THEN 'Self-sufficient exporter'
            WHEN s.fuel_imports_pct_merchandise
                 > s.fuel_exports_pct_merchandise      THEN 'Import dependent'
            ELSE 'Diversified'
        END                                            AS position
FROM    country_latest_snapshot s
JOIN    dim_country c USING (iso3)
WHERE   s.fuel_exports_pct_merchandise IS NOT NULL
ORDER BY s.fuel_imports_pct_merchandise DESC;


-- -----------------------------------------------------------------------------
-- Q2. Years spent in the refining-gap archetype per country (aggregation).
-- Persistence matters: a one-off spike is a price shock, a decade is structural.
-- -----------------------------------------------------------------------------
SELECT  c.country,
        c.opec_member,
        SUM(f.refining_gap_flag)                                 AS refining_gap_years,
        COUNT(f.fuel_imports_pct_merchandise)                    AS years_with_trade_data,
        ROUND(100.0 * SUM(f.refining_gap_flag)
              / NULLIF(COUNT(f.fuel_imports_pct_merchandise), 0), 1) AS pct_of_observed_years,
        MIN(CASE WHEN f.refining_gap_flag = 1 THEN f.year END)   AS first_gap_year,
        MAX(CASE WHEN f.refining_gap_flag = 1 THEN f.year END)   AS last_gap_year
FROM    fact_energy_indicators f
JOIN    dim_country c USING (iso3)
GROUP BY c.country, c.opec_member
HAVING  SUM(f.refining_gap_flag) > 0
ORDER BY refining_gap_years DESC, pct_of_observed_years DESC;


-- -----------------------------------------------------------------------------
-- Q3. Africa vs USA benchmark by year (view + join to USA row).
-- How far is the African average from the US on electricity access, fuel
-- import burden and income?
-- -----------------------------------------------------------------------------
SELECT  a.year,
        a.avg_electricity_access_pct                               AS africa_elec_access_pct,
        u.avg_electricity_access_pct                               AS usa_elec_access_pct,
        ROUND(u.avg_electricity_access_pct
              - a.avg_electricity_access_pct, 1)                   AS access_gap_pp,
        a.avg_fuel_imports_pct                                     AS africa_fuel_imports_pct,
        u.avg_fuel_imports_pct                                     AS usa_fuel_imports_pct,
        ROUND(u.avg_gdp_per_capita_usd / a.avg_gdp_per_capita_usd, 1) AS usa_income_multiple
FROM    v_regional_yearly a
JOIN    v_regional_yearly u
        ON u.year = a.year AND u.region = 'North America'
WHERE   a.region = 'Africa'
  AND   a.year BETWEEN 2016 AND 2025
ORDER BY a.year;


-- -----------------------------------------------------------------------------
-- Q4. Rank African countries by fuel-import burden within each year
-- (window function RANK + share of the regional total).
-- -----------------------------------------------------------------------------
WITH ranked AS (
    SELECT  f.year,
            c.country,
            ROUND(f.fuel_imports_pct_merchandise, 1)                       AS fuel_imports_pct,
            RANK() OVER (PARTITION BY f.year
                         ORDER BY f.fuel_imports_pct_merchandise DESC)     AS burden_rank,
            ROUND(AVG(f.fuel_imports_pct_merchandise)
                  OVER (PARTITION BY f.year), 1)                           AS africa_avg_pct
    FROM    fact_energy_indicators f
    JOIN    dim_country c USING (iso3)
    WHERE   c.region = 'Africa'
      AND   f.fuel_imports_pct_merchandise IS NOT NULL
)
SELECT  year, burden_rank, country, fuel_imports_pct, africa_avg_pct,
        ROUND(fuel_imports_pct - africa_avg_pct, 1) AS vs_africa_avg_pp
FROM    ranked
WHERE   burden_rank <= 3
  AND   year >= 2020
ORDER BY year DESC, burden_rank;


-- -----------------------------------------------------------------------------
-- Q5. Oil-rent volatility and the 2014-16 / 2020 price crashes
-- (window functions LAG + rolling 3-year average).
-- -----------------------------------------------------------------------------
WITH rents AS (
    SELECT  c.country,
            f.year,
            f.oil_rents_pct_gdp,
            LAG(f.oil_rents_pct_gdp) OVER w                         AS prev_year,
            AVG(f.oil_rents_pct_gdp) OVER (PARTITION BY f.iso3 ORDER BY f.year
                                           ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
                                                                    AS rolling_3y_avg
    FROM    fact_energy_indicators f
    JOIN    dim_country c USING (iso3)
    WHERE   f.oil_rents_pct_gdp IS NOT NULL
    WINDOW  w AS (PARTITION BY f.iso3 ORDER BY f.year)
)
SELECT  country,
        year,
        ROUND(oil_rents_pct_gdp, 2)                 AS oil_rents_pct_gdp,
        ROUND(oil_rents_pct_gdp - prev_year, 2)     AS yoy_change_pp,
        ROUND(rolling_3y_avg, 2)                    AS rolling_3y_avg
FROM    rents
WHERE   country IN ('Nigeria', 'Angola', 'Libya', 'Algeria', 'Gabon', 'United States')
  AND   year IN (2014, 2015, 2016, 2019, 2020, 2021)
ORDER BY country, year;


-- -----------------------------------------------------------------------------
-- Q6. Resource wealth vs energy access: do oil rents translate into power?
-- Average oil rents (2012-2021) joined to latest electricity access.
-- -----------------------------------------------------------------------------
WITH rents AS (
    SELECT  iso3,
            ROUND(AVG(oil_rents_pct_gdp), 2) AS avg_oil_rents_pct_gdp
    FROM    fact_energy_indicators
    WHERE   year BETWEEN 2012 AND 2021
    GROUP BY iso3
)
SELECT  c.country,
        r.avg_oil_rents_pct_gdp,
        ROUND(s.electricity_access_pct, 1)               AS electricity_access_pct,
        s.electricity_access_pct_year                    AS access_year,
        ROUND(s.gdp_per_capita_usd, 0)                   AS gdp_per_capita_usd,
        CASE WHEN r.avg_oil_rents_pct_gdp >= 10
              AND s.electricity_access_pct < 60 THEN 'Resource-rich, energy-poor'
             WHEN r.avg_oil_rents_pct_gdp >= 10 THEN 'Resource-rich, electrified'
             WHEN s.electricity_access_pct < 60 THEN 'Low rents, energy-poor'
             ELSE 'Low rents, electrified'
        END                                              AS segment
FROM    rents r
JOIN    country_latest_snapshot s USING (iso3)
JOIN    dim_country c USING (iso3)
ORDER BY r.avg_oil_rents_pct_gdp DESC;


-- -----------------------------------------------------------------------------
-- Q7. Electricity-access progress 2015 -> latest, with percentile position
-- (conditional aggregation + NTILE window).
-- -----------------------------------------------------------------------------
WITH bounds AS (
    SELECT  iso3,
            MAX(CASE WHEN year = 2015 THEN electricity_access_pct END) AS access_2015,
            MAX(CASE WHEN year = 2024 THEN electricity_access_pct END) AS access_2024
    FROM    fact_energy_indicators
    GROUP BY iso3
)
SELECT  c.country,
        ROUND(b.access_2015, 1)                                AS access_2015,
        ROUND(b.access_2024, 1)                                AS access_2024,
        ROUND(b.access_2024 - b.access_2015, 1)                AS gain_pp,
        ROUND((b.access_2024 - b.access_2015) / 9.0, 2)        AS avg_gain_pp_per_year,
        NTILE(4) OVER (ORDER BY b.access_2024 - b.access_2015 DESC) AS progress_quartile
FROM    bounds b
JOIN    dim_country c USING (iso3)
WHERE   b.access_2015 IS NOT NULL AND b.access_2024 IS NOT NULL
ORDER BY gain_pp DESC;


-- -----------------------------------------------------------------------------
-- Q8. Sub-regional scorecard (multi-table join + aggregation), latest values.
-- Summarises the refining gap, fuel trade and access picture by sub-region,
-- with the USA as the benchmark row.
-- -----------------------------------------------------------------------------
SELECT  c.subregion,
        COUNT(*)                                                AS countries,
        SUM(c.opec_member)                                      AS opec_members,
        ROUND(AVG(s.fuel_exports_pct_merchandise), 1)           AS avg_fuel_exports_pct,
        ROUND(AVG(s.fuel_imports_pct_merchandise), 1)           AS avg_fuel_imports_pct,
        ROUND(AVG(s.electricity_access_pct), 1)                 AS avg_elec_access_pct,
        ROUND(MIN(s.electricity_access_pct), 1)                 AS min_elec_access_pct,
        ROUND(AVG(s.gdp_per_capita_usd), 0)                     AS avg_gdp_per_capita_usd,
        (SELECT COUNT(DISTINCT f.iso3)
           FROM fact_energy_indicators f
           JOIN dim_country c2 USING (iso3)
          WHERE c2.subregion = c.subregion
            AND f.refining_gap_flag = 1)                        AS countries_ever_in_refining_gap
FROM    country_latest_snapshot s
JOIN    dim_country c USING (iso3)
GROUP BY c.subregion
ORDER BY CASE c.subregion WHEN 'North America' THEN 1 ELSE 0 END,
         avg_elec_access_pct;
