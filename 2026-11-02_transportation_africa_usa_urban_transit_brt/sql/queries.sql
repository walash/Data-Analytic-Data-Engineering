-- =============================================================================
-- Urban Transit & BRT Systems: Africa-USA - Analytical Queries
-- Database : data/transit_analytics.db (SQLite, built by src/loading/load.py)
-- Tables   : brt_systems, urban_indicators, brt_enriched
-- Run with : sqlite3 data/transit_analytics.db < sql/queries.sql
-- =============================================================================

.headers on
.mode column

-- -----------------------------------------------------------------------------
-- Query 1: Top 10 BRT systems by daily ridership
-- Which systems move the most people per day, and where are they?
-- -----------------------------------------------------------------------------
SELECT
    system_name,
    city,
    country,
    continent,
    CAST(daily_ridership AS INTEGER)    AS daily_ridership,
    corridor_length_km,
    year_opened
FROM brt_systems
ORDER BY daily_ridership DESC
LIMIT 10;

-- -----------------------------------------------------------------------------
-- Query 2: Average ridership per km by continent
-- Corridor productivity: riders carried per km of BRT infrastructure.
-- Network-level ratio (total riders / total km) is shown next to the simple
-- mean so that a few very long US networks do not dominate the comparison.
-- -----------------------------------------------------------------------------
SELECT
    continent,
    COUNT(*)                                              AS num_systems,
    ROUND(AVG(daily_ridership / corridor_length_km), 1)   AS avg_ridership_per_km,
    ROUND(SUM(daily_ridership) / SUM(corridor_length_km), 1) AS network_ridership_per_km,
    ROUND(SUM(corridor_length_km), 1)                     AS total_corridor_km,
    CAST(SUM(daily_ridership) AS INTEGER)                 AS total_daily_ridership
FROM brt_systems
WHERE corridor_length_km > 0
GROUP BY continent
ORDER BY avg_ridership_per_km DESC;

-- -----------------------------------------------------------------------------
-- Query 3: BRT systems opened per year (trend analysis)
-- Opening trend by year and continent, with a cumulative count of systems.
-- -----------------------------------------------------------------------------
SELECT
    year_opened,
    SUM(CASE WHEN continent = 'Africa' THEN 1 ELSE 0 END)        AS africa_openings,
    SUM(CASE WHEN continent = 'North America' THEN 1 ELSE 0 END) AS usa_openings,
    COUNT(*)                                                      AS total_openings,
    SUM(COUNT(*)) OVER (ORDER BY year_opened)                     AS cumulative_systems
FROM brt_systems
GROUP BY year_opened
ORDER BY year_opened;

-- -----------------------------------------------------------------------------
-- Query 4: Countries with BRT systems and their urban population (JOIN)
-- Joins the BRT inventory to the latest World Bank urban indicators per country.
-- -----------------------------------------------------------------------------
WITH latest_urban AS (
    SELECT u.*
    FROM urban_indicators u
    WHERE u.year = (SELECT MAX(u2.year)
                    FROM urban_indicators u2
                    WHERE u2.iso3 = u.iso3 AND u2.urban_population IS NOT NULL)
)
SELECT
    b.country,
    b.iso3,
    COUNT(b.system_id)                              AS num_brt_systems,
    CAST(SUM(b.daily_ridership) AS INTEGER)         AS total_daily_ridership,
    lu.year                                         AS indicator_year,
    CAST(lu.urban_population AS INTEGER)            AS urban_population,
    ROUND(lu.urban_pct_of_total, 1)                 AS urban_pct_of_total,
    ROUND(SUM(b.daily_ridership) * 1000.0 / lu.urban_population, 2) AS brt_riders_per_1k_urban
FROM brt_systems b
JOIN latest_urban lu ON lu.iso3 = b.iso3
GROUP BY b.country, b.iso3, lu.year, lu.urban_population, lu.urban_pct_of_total
ORDER BY urban_population DESC;

-- -----------------------------------------------------------------------------
-- Query 5: Ridership efficiency - ridership per station, ranked
-- RANK() overall plus rank within each continent.
-- -----------------------------------------------------------------------------
SELECT
    system_name,
    city,
    continent,
    CAST(num_stations AS INTEGER)                               AS num_stations,
    ROUND(daily_ridership / num_stations, 1)                    AS ridership_per_station,
    RANK() OVER (ORDER BY daily_ridership / num_stations DESC)  AS overall_rank,
    RANK() OVER (PARTITION BY continent
                 ORDER BY daily_ridership / num_stations DESC)  AS continent_rank
FROM brt_systems
WHERE num_stations > 0
ORDER BY overall_rank;

-- -----------------------------------------------------------------------------
-- Query 6: Window function - running total of BRT corridor length by continent
-- Shows how each continent's BRT network accumulated over time.
-- -----------------------------------------------------------------------------
SELECT
    continent,
    year_opened,
    system_name,
    corridor_length_km,
    ROUND(SUM(corridor_length_km) OVER (
        PARTITION BY continent
        ORDER BY year_opened, system_id
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 1)   AS running_corridor_km,
    ROUND(100.0 * corridor_length_km
          / SUM(corridor_length_km) OVER (PARTITION BY continent), 1) AS pct_of_continent_network
FROM brt_systems
ORDER BY continent, year_opened, system_id;

-- -----------------------------------------------------------------------------
-- Query 7: Fleet utilization analysis (ridership per fleet vehicle)
-- Buckets systems by daily riders per bus and compares to continent average.
-- -----------------------------------------------------------------------------
SELECT
    system_name,
    city,
    continent,
    CAST(fleet_size AS INTEGER)                    AS fleet_size,
    fleet_utilization_ratio                        AS riders_per_vehicle,
    ROUND(AVG(fleet_utilization_ratio) OVER (PARTITION BY continent), 1) AS continent_avg,
    ROUND(fleet_utilization_ratio
          - AVG(fleet_utilization_ratio) OVER (PARTITION BY continent), 1) AS diff_vs_continent_avg,
    CASE
        WHEN fleet_utilization_ratio >= 800 THEN 'Very high (>=800)'
        WHEN fleet_utilization_ratio >= 400 THEN 'High (400-799)'
        WHEN fleet_utilization_ratio >= 250 THEN 'Moderate (250-399)'
        ELSE 'Low (<250)'
    END                                            AS utilization_band
FROM brt_enriched
WHERE fleet_utilization_ratio IS NOT NULL
ORDER BY riders_per_vehicle DESC;

-- -----------------------------------------------------------------------------
-- Query 8: Urban growth rate vs BRT adoption
-- Compares average urban growth, urbanisation and GDP per capita (latest year)
-- for countries that operate a BRT system versus those that do not.
-- -----------------------------------------------------------------------------
WITH latest AS (
    SELECT u.*
    FROM urban_indicators u
    WHERE u.year = (SELECT MAX(u2.year) FROM urban_indicators u2 WHERE u2.iso3 = u.iso3)
),
flagged AS (
    SELECT
        l.*,
        CASE WHEN l.iso3 IN (SELECT DISTINCT iso3 FROM brt_systems)
             THEN 'Has BRT' ELSE 'No BRT' END AS brt_status
    FROM latest l
)
SELECT
    brt_status,
    COUNT(*)                                   AS num_countries,
    ROUND(AVG(urban_growth_rate_pct), 2)       AS avg_urban_growth_pct,
    ROUND(AVG(urban_pct_of_total), 1)          AS avg_urban_pct_of_total,
    ROUND(AVG(gdp_per_capita_usd), 0)          AS avg_gdp_per_capita_usd,
    CAST(SUM(urban_population) AS INTEGER)     AS total_urban_population
FROM flagged
GROUP BY brt_status
ORDER BY brt_status;
