-- ===========================================================================
-- Inland Waterways Transportation: Africa & USA - analytical queries
-- Target database : data/waterways.db (SQLite >= 3.25 for window functions)
-- Run with        : sqlite3 data/waterways.db < sql/queries.sql
-- Units           : freight_thousand_tons = thousand metric tons,
--                   utilization_ratio     = tons moved per navigable km,
--                   waterway_density      = navigable km per 1,000 sq km
-- ===========================================================================

.headers on
.mode column

-- ---------------------------------------------------------------------------
-- Q1: Top 10 countries by navigable waterway km
-- Ranks the inland waterway networks and shows how much of each network is
-- navigable and how dense it is relative to land area.
-- ---------------------------------------------------------------------------
SELECT
    RANK() OVER (ORDER BY navigable_km DESC)  AS rank_navigable,
    country,
    region,
    total_waterway_km,
    navigable_km,
    ROUND(navigable_share * 100, 1)           AS navigable_pct,
    waterway_density,
    major_rivers
FROM waterway_infrastructure
WHERE navigable_km > 0
ORDER BY navigable_km DESC
LIMIT 10;

-- ---------------------------------------------------------------------------
-- Q2: US freight tonnage trend by commodity (2010-2023)
-- Annual tonnage per commodity across the Mississippi River System and the
-- Ohio River (Ohio River series in the extract starts in 2020).
-- ---------------------------------------------------------------------------
SELECT
    year,
    commodity,
    ROUND(SUM(tonnage_million), 1)                                   AS tonnage_million,
    ROUND(SUM(CASE WHEN waterway = 'Mississippi River System'
                   THEN tonnage_million ELSE 0 END), 1)              AS mississippi_million,
    ROUND(SUM(CASE WHEN waterway = 'Ohio River'
                   THEN tonnage_million ELSE 0 END), 1)              AS ohio_million
FROM us_freight
WHERE year BETWEEN 2010 AND 2023
GROUP BY year, commodity
ORDER BY commodity, year;

-- ---------------------------------------------------------------------------
-- Q3: Africa waterway freight growth rate by country (window function)
-- Compares each country's first and latest observed year using
-- FIRST_VALUE / LAST_VALUE and derives total and compound annual growth.
-- ---------------------------------------------------------------------------
WITH country_year AS (
    SELECT country, year, SUM(freight_thousand_tons) AS freight
    FROM africa_freight
    GROUP BY country, year
),
bounds AS (
    SELECT DISTINCT
        country,
        FIRST_VALUE(year)    OVER w AS first_year,
        LAST_VALUE(year)     OVER w AS last_year,
        FIRST_VALUE(freight) OVER w AS first_freight,
        LAST_VALUE(freight)  OVER w AS last_freight
    FROM country_year
    WINDOW w AS (PARTITION BY country ORDER BY year
                 ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
)
SELECT
    country,
    first_year,
    last_year,
    first_freight                                                     AS first_freight_kt,
    last_freight                                                      AS last_freight_kt,
    ROUND((last_freight - first_freight) * 100.0 / first_freight, 2)  AS total_growth_pct,
    ROUND((POWER(last_freight * 1.0 / first_freight,
                 1.0 / (last_year - first_year)) - 1) * 100, 2)       AS cagr_pct,
    RANK() OVER (ORDER BY (last_freight - first_freight) * 1.0 / first_freight DESC) AS growth_rank
FROM bounds
ORDER BY growth_rank;

-- ---------------------------------------------------------------------------
-- Q4: Container port traffic vs waterway infrastructure correlation
-- Joins average recent (2018+) TEU with network size and computes the
-- Pearson correlation coefficient between navigable km and TEU in SQL.
-- ---------------------------------------------------------------------------
WITH paired AS (
    SELECT
        w.country,
        w.region,
        w.navigable_km,
        AVG(c.container_teu) AS avg_teu
    FROM waterway_infrastructure w
    JOIN container_traffic c ON c.iso3 = w.iso3
    WHERE c.year >= 2018
    GROUP BY w.iso3
),
stats AS (
    SELECT
        COUNT(*)                         AS n,
        AVG(navigable_km)                AS mx,
        AVG(avg_teu)                     AS my,
        AVG(navigable_km * navigable_km) AS mxx,
        AVG(avg_teu * avg_teu)           AS myy,
        AVG(navigable_km * avg_teu)      AS mxy
    FROM paired
)
SELECT
    p.country,
    p.region,
    p.navigable_km,
    ROUND(p.avg_teu, 0)                                        AS avg_teu_2018_plus,
    ROUND(p.avg_teu / NULLIF(p.navigable_km, 0), 1)            AS teu_per_navigable_km,
    ROUND((s.mxy - s.mx * s.my)
          / (SQRT(s.mxx - s.mx * s.mx) * SQRT(s.myy - s.my * s.my)), 3) AS pearson_r_all_countries
FROM paired p
CROSS JOIN stats s
ORDER BY p.avg_teu DESC;

-- ---------------------------------------------------------------------------
-- Q5: Countries with highest waterway utilization (freight per navigable km)
-- Uses the latest year with recorded inland waterway freight per country.
-- ---------------------------------------------------------------------------
WITH latest AS (
    SELECT iso3, MAX(year) AS year
    FROM country_metrics
    WHERE waterway_freight_thousand_tons IS NOT NULL
    GROUP BY iso3
)
SELECT
    m.country_name,
    m.region,
    m.year,
    m.waterway_freight_thousand_tons,
    m.navigable_km,
    m.utilization_ratio                       AS tons_per_navigable_km,
    m.freight_per_1000_pop                    AS tons_per_1000_people,
    DENSE_RANK() OVER (ORDER BY m.utilization_ratio DESC) AS utilization_rank
FROM country_metrics m
JOIN latest l ON l.iso3 = m.iso3 AND l.year = m.year
WHERE m.navigable_km > 0
ORDER BY tons_per_navigable_km DESC;

-- ---------------------------------------------------------------------------
-- Q6: Year-over-year freight change using LAG window function
-- Per waterway (Africa) and per US waterway system, the absolute and
-- percentage change versus the previous observed year.
-- ---------------------------------------------------------------------------
WITH series AS (
    SELECT 'Africa' AS region, country, waterway, year,
           SUM(freight_thousand_tons) AS freight_kt
    FROM africa_freight
    GROUP BY country, waterway, year
    UNION ALL
    SELECT 'USA', 'United States', waterway, year,
           SUM(freight_thousand_tons)
    FROM us_freight
    GROUP BY waterway, year
)
SELECT
    region,
    country,
    waterway,
    year,
    freight_kt,
    LAG(year)       OVER w                                         AS prev_year,
    LAG(freight_kt) OVER w                                         AS prev_freight_kt,
    freight_kt - LAG(freight_kt) OVER w                            AS change_kt,
    ROUND((freight_kt - LAG(freight_kt) OVER w) * 100.0
          / LAG(freight_kt) OVER w, 2)                             AS yoy_change_pct
FROM series
WINDOW w AS (PARTITION BY country, waterway ORDER BY year)
ORDER BY region, country, waterway, year;

-- ---------------------------------------------------------------------------
-- Q7: Regional comparison - Africa vs USA average freight per waterway km
-- Restricted to overlapping years (2015-2022) so both regions are measured
-- over the same period. Gap multiple = USA ratio / Africa ratio.
-- ---------------------------------------------------------------------------
WITH regional AS (
    SELECT
        region,
        COUNT(DISTINCT iso3)                                   AS countries,
        SUM(waterway_freight_thousand_tons) * 1000.0           AS freight_tons,
        SUM(navigable_km)                                      AS navigable_km_years,
        AVG(utilization_ratio)                                 AS avg_country_utilization,
        AVG(freight_per_1000_pop)                              AS avg_tons_per_1000_people
    FROM country_metrics
    WHERE waterway_freight_thousand_tons IS NOT NULL
      AND navigable_km > 0
      AND year BETWEEN 2015 AND 2022
    GROUP BY region
)
SELECT
    region,
    countries,
    ROUND(freight_tons / navigable_km_years, 1)  AS pooled_tons_per_km,
    ROUND(avg_country_utilization, 1)            AS avg_country_tons_per_km,
    ROUND(avg_tons_per_1000_people, 1)           AS avg_tons_per_1000_people,
    ROUND((freight_tons / navigable_km_years)
          / (SELECT freight_tons / navigable_km_years FROM regional WHERE region = 'Africa'), 1)
                                                 AS multiple_of_africa
FROM regional
ORDER BY pooled_tons_per_km DESC;

-- ---------------------------------------------------------------------------
-- Q8: Top African waterways by passenger traffic with freight ranking
-- Latest year per waterway-country, ranking passengers and freight side by
-- side to reveal passenger-dominant versus freight-dominant corridors.
-- ---------------------------------------------------------------------------
WITH latest AS (
    SELECT a.*
    FROM africa_freight a
    JOIN (SELECT iso3, waterway, MAX(year) AS year
          FROM africa_freight GROUP BY iso3, waterway) l
      ON l.iso3 = a.iso3 AND l.waterway = a.waterway AND l.year = a.year
)
SELECT
    waterway,
    country,
    year,
    passengers_thousand,
    freight_thousand_tons,
    ports_count,
    passengers_per_port,
    RANK() OVER (ORDER BY passengers_thousand DESC)    AS passenger_rank,
    RANK() OVER (ORDER BY freight_thousand_tons DESC)  AS freight_rank,
    ROUND(passengers_thousand / freight_thousand_tons, 2) AS passengers_per_ton_ratio
FROM latest
ORDER BY passenger_rank;
