-- ===========================================================================
-- African Asylum Seekers & Immigration Flows to the USA - analytical queries
-- Database : data/africa_usa_immigration.db (built by src/loading/load.py)
-- Run      : sqlite3 data/africa_usa_immigration.db < sql/queries.sql
-- Source   : World Bank WDI (SM.POP.NETM, SM.POP.TOTL, SM.POP.TOTL.ZS,
--            BX.TRF.PWKR.DT.GD.ZS), 2021-2025.
-- Note     : World Bank net migration is a total (all destinations). Negative
--            values = more people leaving than arriving; these net emigration
--            figures are the outflow pool from which US-bound migrants and
--            asylum seekers are drawn.
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- Q1. Top 10 African countries by cumulative net emigration (2021-2025)
--     The largest origin pools for African migrants and asylum seekers.
-- ---------------------------------------------------------------------------
SELECT
    country_name,
    subregion,
    SUM(net_migration)                AS cumulative_net_migration,
    ROUND(AVG(net_migration), 0)      AS avg_annual_net_migration,
    MIN(net_migration)                AS worst_year_outflow
FROM migration_flows
WHERE region = 'Africa'
GROUP BY iso3, country_name, subregion
HAVING SUM(net_migration) < 0
ORDER BY cumulative_net_migration ASC
LIMIT 10;


-- ---------------------------------------------------------------------------
-- Q2. Year-over-year trend: total African net migration vs USA net migration
--     LAG() gives the absolute and % change from the previous year.
-- ---------------------------------------------------------------------------
WITH yearly AS (
    SELECT
        year,
        SUM(CASE WHEN region = 'Africa' THEN net_migration ELSE 0 END) AS africa_net_migration,
        SUM(CASE WHEN region = 'USA'    THEN net_migration ELSE 0 END) AS usa_net_migration
    FROM migration_flows
    GROUP BY year
)
SELECT
    year,
    africa_net_migration,
    africa_net_migration - LAG(africa_net_migration) OVER (ORDER BY year) AS africa_yoy_change,
    usa_net_migration,
    usa_net_migration - LAG(usa_net_migration) OVER (ORDER BY year)       AS usa_yoy_change,
    ROUND(100.0 * (usa_net_migration - LAG(usa_net_migration) OVER (ORDER BY year))
          / LAG(usa_net_migration) OVER (ORDER BY year), 1)               AS usa_yoy_pct
FROM yearly
ORDER BY year;


-- ---------------------------------------------------------------------------
-- Q3. Countries with the highest remittance dependency (average % of GDP)
--     High dependency signals economies tied to their diaspora abroad.
-- ---------------------------------------------------------------------------
SELECT
    country_name,
    subregion,
    ROUND(AVG(remittances_pct_gdp), 2)  AS avg_remittances_pct_gdp,
    ROUND(MAX(remittances_pct_gdp), 2)  AS peak_remittances_pct_gdp,
    COUNT(remittances_pct_gdp)          AS years_reported,
    (SELECT r2.remittance_dependency_category
       FROM remittance_flows r2
      WHERE r2.iso3 = r.iso3 AND r2.remittances_pct_gdp IS NOT NULL
      ORDER BY r2.year DESC LIMIT 1)    AS latest_category
FROM remittance_flows r
WHERE remittances_pct_gdp IS NOT NULL
GROUP BY iso3, country_name, subregion
ORDER BY avg_remittances_pct_gdp DESC;


-- ---------------------------------------------------------------------------
-- Q4. Migrant stock vs remittances (Pearson correlation computed in SQL)
--     Uses the latest reported migrant stock and average remittances per country.
-- ---------------------------------------------------------------------------
WITH pairs AS (
    SELECT latest_migrant_stock AS x, avg_remittances_pct_gdp AS y
    FROM country_profiles
    WHERE latest_migrant_stock IS NOT NULL AND avg_remittances_pct_gdp IS NOT NULL
),
stats AS (
    SELECT COUNT(*) AS n, AVG(x) AS mx, AVG(y) AS my FROM pairs
)
SELECT
    s.n                                                         AS countries,
    ROUND(SUM((p.x - s.mx) * (p.y - s.my)) /
          (SQRT(SUM((p.x - s.mx) * (p.x - s.mx))) *
           SQRT(SUM((p.y - s.my) * (p.y - s.my)))), 3)         AS pearson_r_stock_vs_remittances
FROM pairs p CROSS JOIN stats s;


-- ---------------------------------------------------------------------------
-- Q5. Window function: rank countries by migration intensity (2024)
--     migration_intensity = net_migration / migrant_stock. Ranked overall and
--     within each sub-region (most negative = strongest outflow pressure).
-- ---------------------------------------------------------------------------
SELECT
    country_name,
    subregion,
    net_migration,
    migrant_stock_total,
    ROUND(migration_intensity, 4)                                    AS migration_intensity,
    RANK() OVER (ORDER BY migration_intensity ASC)                   AS overall_outflow_rank,
    RANK() OVER (PARTITION BY subregion ORDER BY migration_intensity ASC) AS subregion_rank
FROM migration_flows
WHERE region = 'Africa'
  AND year = 2024
  AND migration_intensity IS NOT NULL
ORDER BY overall_outflow_rank;


-- ---------------------------------------------------------------------------
-- Q6. Regional aggregations: North, West, East, Central and Southern Africa
-- ---------------------------------------------------------------------------
SELECT
    subregion,
    COUNT(DISTINCT iso3)                                         AS countries,
    SUM(net_migration)                                           AS cumulative_net_migration,
    SUM(CASE WHEN net_migration < 0 THEN net_migration ELSE 0 END) AS gross_outflow_total,
    SUM(CASE WHEN migration_direction = 'net_emigration' THEN 1 ELSE 0 END) AS emigration_country_years,
    ROUND(AVG(migrant_stock_pct_pop), 2)                         AS avg_migrant_stock_pct_pop
FROM migration_flows
WHERE region = 'Africa'
GROUP BY subregion
ORDER BY cumulative_net_migration ASC;


-- ---------------------------------------------------------------------------
-- Q7. Growing vs declining net emigration: first year (2021) vs last year (2025)
--     "Growing outflow" = net migration became more negative over the period.
-- ---------------------------------------------------------------------------
WITH bounds AS (
    SELECT
        iso3,
        country_name,
        subregion,
        MAX(CASE WHEN year = 2021 THEN net_migration END) AS nm_2021,
        MAX(CASE WHEN year = 2025 THEN net_migration END) AS nm_2025
    FROM migration_flows
    WHERE region = 'Africa'
    GROUP BY iso3, country_name, subregion
)
SELECT
    country_name,
    subregion,
    nm_2021,
    nm_2025,
    nm_2025 - nm_2021 AS change_2021_2025,
    CASE
        WHEN nm_2025 < nm_2021 THEN 'growing outflow / shrinking inflow'
        WHEN nm_2025 > nm_2021 THEN 'declining outflow / growing inflow'
        ELSE 'stable'
    END AS trend
FROM bounds
ORDER BY change_2021_2025 ASC;


-- ---------------------------------------------------------------------------
-- Q8. USA immigration absorption metrics
--     Compares US net immigration with the total African net emigration pool
--     each year (how large Africa's outflow is relative to US intake).
-- ---------------------------------------------------------------------------
WITH usa AS (
    SELECT year, net_migration AS usa_net_migration
    FROM migration_flows WHERE region = 'USA'
),
africa AS (
    SELECT
        year,
        -SUM(CASE WHEN net_migration < 0 THEN net_migration ELSE 0 END) AS africa_emigration_pool,
        SUM(CASE WHEN net_migration < 0 THEN 1 ELSE 0 END)              AS emigration_countries
    FROM migration_flows WHERE region = 'Africa'
    GROUP BY year
)
SELECT
    u.year,
    u.usa_net_migration,
    a.africa_emigration_pool,
    a.emigration_countries,
    ROUND(1.0 * a.africa_emigration_pool / u.usa_net_migration, 3) AS africa_pool_to_usa_intake_ratio,
    SUM(u.usa_net_migration) OVER (ORDER BY u.year)                AS usa_cumulative_net_migration
FROM usa u
JOIN africa a ON a.year = u.year
ORDER BY u.year;
