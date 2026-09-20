-- =====================================================================
-- Africa-USA Air Cargo & Freight Routes -- Analytical SQL Queries
-- Target engine: SQLite (data/air_cargo.db)
-- Each query is self-contained and can be run independently, e.g.:
--     sqlite3 data/air_cargo.db < sql/queries.sql
-- =====================================================================


-- ---------------------------------------------------------------------
-- Query 1: Top 10 African airports by scheduled service availability.
-- Airports flagged with scheduled_service = 'yes' offer commercial flights;
-- we rank African airports and show their location details.
-- ---------------------------------------------------------------------
SELECT
    name,
    iata_code,
    iso_country,
    municipality,
    scheduled_service
FROM airports
WHERE region = 'Africa'
  AND scheduled_service = 'yes'
ORDER BY iata_code IS NULL, name
LIMIT 10;


-- ---------------------------------------------------------------------
-- Query 2: Top 10 US hub airports by number of Africa-USA route endpoints.
-- Counts how often each US airport appears as an endpoint on an
-- Africa <-> USA route, revealing the primary US gateways.
-- ---------------------------------------------------------------------
SELECT
    us_airport,
    COUNT(*) AS route_count
FROM (
    SELECT dst_airport AS us_airport
    FROM routes
    WHERE direction = 'Africa->USA'
    UNION ALL
    SELECT src_airport AS us_airport
    FROM routes
    WHERE direction = 'USA->Africa'
) AS us_endpoints
GROUP BY us_airport
ORDER BY route_count DESC
LIMIT 10;


-- ---------------------------------------------------------------------
-- Query 3: Africa-USA direct routes broken down by airline.
-- Shows which carriers operate the most direct Africa <-> USA services.
-- ---------------------------------------------------------------------
SELECT
    airline,
    COUNT(*) AS num_routes,
    SUM(CASE WHEN direction = 'Africa->USA' THEN 1 ELSE 0 END) AS africa_to_usa,
    SUM(CASE WHEN direction = 'USA->Africa' THEN 1 ELSE 0 END) AS usa_to_africa
FROM routes
GROUP BY airline
ORDER BY num_routes DESC;


-- ---------------------------------------------------------------------
-- Query 4: African countries ranked by most recent air-freight volume.
-- Uses the World Bank IS.AIR.GOOD.MT.K1 indicator (million ton-km) and
-- selects each country's latest available observation.
-- ---------------------------------------------------------------------
SELECT
    f.country,
    f.country_iso3,
    f.year,
    f.freight_mt_km
FROM air_freight_stats AS f
JOIN (
    SELECT country_iso3, MAX(year) AS latest_year
    FROM air_freight_stats
    WHERE region = 'Africa'
    GROUP BY country_iso3
) AS latest
  ON f.country_iso3 = latest.country_iso3
 AND f.year = latest.latest_year
WHERE f.region = 'Africa'
ORDER BY f.freight_mt_km DESC
LIMIT 15;


-- ---------------------------------------------------------------------
-- Query 5: Year-over-year freight growth for top African countries.
-- Uses LAG() to compute the percentage change versus the previous year
-- for each African country's freight volume.
-- ---------------------------------------------------------------------
SELECT
    country,
    year,
    freight_mt_km,
    LAG(freight_mt_km) OVER (
        PARTITION BY country_iso3 ORDER BY year
    ) AS prev_year_freight,
    ROUND(
        100.0 * (
            freight_mt_km - LAG(freight_mt_km) OVER (
                PARTITION BY country_iso3 ORDER BY year
            )
        ) / NULLIF(
            LAG(freight_mt_km) OVER (
                PARTITION BY country_iso3 ORDER BY year
            ), 0
        ), 2
    ) AS yoy_growth_pct
FROM air_freight_stats
WHERE region = 'Africa'
ORDER BY country, year;


-- ---------------------------------------------------------------------
-- Query 6: Window function -- rank African airports within each country.
-- Ranks African airports alphabetically inside their country using
-- ROW_NUMBER(), illustrating per-partition ordering.
-- ---------------------------------------------------------------------
SELECT
    iso_country,
    name,
    iata_code,
    ROW_NUMBER() OVER (
        PARTITION BY iso_country ORDER BY name
    ) AS rank_in_country
FROM airports
WHERE region = 'Africa'
  AND scheduled_service = 'yes'
ORDER BY iso_country, rank_in_country;


-- ---------------------------------------------------------------------
-- Query 7: African airports with the most international connections.
-- Counts distinct destination airports reached from each African airport
-- across the full route network.
-- ---------------------------------------------------------------------
SELECT
    a.name           AS airport_name,
    a.iata_code      AS iata_code,
    a.iso_country    AS country,
    COUNT(DISTINCT r.dst_airport) AS distinct_destinations
FROM airports AS a
JOIN routes AS r
  ON a.iata_code = r.src_airport
WHERE a.region = 'Africa'
GROUP BY a.iata_code, a.name, a.iso_country
ORDER BY distinct_destinations DESC
LIMIT 10;


-- ---------------------------------------------------------------------
-- Query 8: Comparison of African vs US aggregate air-freight metrics.
-- Aggregates the latest-year freight totals for each region.
-- ---------------------------------------------------------------------
SELECT
    f.region,
    COUNT(DISTINCT f.country_iso3) AS num_countries,
    ROUND(SUM(f.freight_mt_km), 2) AS total_freight_mt_km,
    ROUND(AVG(f.freight_mt_km), 2) AS avg_freight_mt_km
FROM air_freight_stats AS f
JOIN (
    SELECT country_iso3, MAX(year) AS latest_year
    FROM air_freight_stats
    GROUP BY country_iso3
) AS latest
  ON f.country_iso3 = latest.country_iso3
 AND f.year = latest.latest_year
WHERE f.region IN ('Africa', 'USA')
GROUP BY f.region
ORDER BY total_freight_mt_km DESC;


-- ---------------------------------------------------------------------
-- Query 9: Top African countries by most recent passenger traffic.
-- Uses the World Bank IS.AIR.PSGR indicator (air passengers carried).
-- ---------------------------------------------------------------------
SELECT
    p.country,
    p.country_iso3,
    p.year,
    p.passengers
FROM air_passenger_stats AS p
JOIN (
    SELECT country_iso3, MAX(year) AS latest_year
    FROM air_passenger_stats
    WHERE region = 'Africa'
    GROUP BY country_iso3
) AS latest
  ON p.country_iso3 = latest.country_iso3
 AND p.year = latest.latest_year
WHERE p.region = 'Africa'
ORDER BY p.passengers DESC
LIMIT 15;


-- ---------------------------------------------------------------------
-- Query 10: Freight-to-passenger ratio analysis for African countries.
-- Joins the latest freight and passenger observations per country to
-- compute a freight-intensity ratio (freight ton-km per passenger).
-- ---------------------------------------------------------------------
WITH latest_freight AS (
    SELECT f.country_iso3, f.country, f.freight_mt_km
    FROM air_freight_stats AS f
    JOIN (
        SELECT country_iso3, MAX(year) AS latest_year
        FROM air_freight_stats
        WHERE region = 'Africa'
        GROUP BY country_iso3
    ) AS lf
      ON f.country_iso3 = lf.country_iso3 AND f.year = lf.latest_year
    WHERE f.region = 'Africa'
),
latest_passengers AS (
    SELECT p.country_iso3, p.passengers
    FROM air_passenger_stats AS p
    JOIN (
        SELECT country_iso3, MAX(year) AS latest_year
        FROM air_passenger_stats
        WHERE region = 'Africa'
        GROUP BY country_iso3
    ) AS lp
      ON p.country_iso3 = lp.country_iso3 AND p.year = lp.latest_year
    WHERE p.region = 'Africa'
)
SELECT
    lf.country,
    lf.freight_mt_km,
    lp.passengers,
    ROUND(
        1000000.0 * lf.freight_mt_km / NULLIF(lp.passengers, 0), 4
    ) AS freight_ton_km_per_passenger
FROM latest_freight AS lf
JOIN latest_passengers AS lp
  ON lf.country_iso3 = lp.country_iso3
ORDER BY freight_ton_km_per_passenger DESC
LIMIT 15;
