-- ============================================================================
-- AI in Agriculture: Crop Yield Prediction & Food Security Analytics
-- Analytical SQL queries for data/agriculture_analytics.db (SQLite)
--
-- Run with:  sqlite3 data/agriculture_analytics.db < sql/queries.sql
-- Tables:    agriculture_metrics (country-year facts), country_summary (aggregates)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. Top 5 African countries by average cereal yield (kg/hectare).
--    Identifies the highest-productivity cereal producers on the continent.
-- ----------------------------------------------------------------------------
SELECT
    country_name,
    ROUND(AVG(cereal_yield_kg_ha), 1) AS avg_cereal_yield_kg_ha,
    COUNT(DISTINCT year)              AS years_observed
FROM agriculture_metrics
WHERE region = 'Africa'
  AND cereal_yield_kg_ha IS NOT NULL
GROUP BY country_name
ORDER BY avg_cereal_yield_kg_ha DESC
LIMIT 5;

-- ----------------------------------------------------------------------------
-- 2. Year-over-year cereal-yield growth using the LAG window function.
--    Computes each country's YoY change to surface momentum and volatility.
-- ----------------------------------------------------------------------------
SELECT
    country_name,
    year,
    cereal_yield_kg_ha,
    LAG(cereal_yield_kg_ha) OVER (
        PARTITION BY country_name ORDER BY year
    ) AS prev_year_yield,
    ROUND(
        (cereal_yield_kg_ha - LAG(cereal_yield_kg_ha) OVER (
            PARTITION BY country_name ORDER BY year))
        * 100.0
        / NULLIF(LAG(cereal_yield_kg_ha) OVER (
            PARTITION BY country_name ORDER BY year), 0),
        2
    ) AS yoy_growth_pct
FROM agriculture_metrics
WHERE cereal_yield_kg_ha IS NOT NULL
ORDER BY country_name, year;

-- ----------------------------------------------------------------------------
-- 3. Countries whose agriculture value added exceeds 20% of GDP.
--    Highlights economies most structurally dependent on agriculture.
-- ----------------------------------------------------------------------------
SELECT
    country_name,
    region,
    ROUND(AVG(agr_value_added_pct), 2) AS avg_agr_value_added_pct
FROM agriculture_metrics
WHERE agr_value_added_pct IS NOT NULL
GROUP BY country_name, region
HAVING AVG(agr_value_added_pct) > 20
ORDER BY avg_agr_value_added_pct DESC;

-- ----------------------------------------------------------------------------
-- 4. Fertilizer efficiency ranking (cereal yield per kg of fertilizer).
--    Ranks countries by how effectively fertilizer converts into yield.
-- ----------------------------------------------------------------------------
SELECT
    country_name,
    region,
    ROUND(AVG(fertilizer_efficiency), 2) AS avg_fertilizer_efficiency,
    ROUND(AVG(cereal_yield_kg_ha), 1)    AS avg_cereal_yield_kg_ha,
    ROUND(AVG(fertilizer_kg_ha), 2)      AS avg_fertilizer_kg_ha
FROM agriculture_metrics
WHERE fertilizer_efficiency IS NOT NULL
GROUP BY country_name, region
ORDER BY avg_fertilizer_efficiency DESC;

-- ----------------------------------------------------------------------------
-- 5. Average food-security score grouped by AI investment priority bucket.
--    Confirms that "Critical" cohorts carry the lowest food-security scores.
-- ----------------------------------------------------------------------------
SELECT
    ai_investment_priority,
    COUNT(DISTINCT country_name)        AS n_countries,
    ROUND(AVG(food_security_score), 2)  AS avg_food_security_score,
    ROUND(MIN(food_security_score), 2)  AS min_food_security_score,
    ROUND(MAX(food_security_score), 2)  AS max_food_security_score
FROM agriculture_metrics
WHERE food_security_score IS NOT NULL
GROUP BY ai_investment_priority
ORDER BY avg_food_security_score ASC;

-- ----------------------------------------------------------------------------
-- 6. Africa vs USA comparison across key agricultural metrics.
--    Benchmarks the African cohort against a high-input reference economy.
-- ----------------------------------------------------------------------------
SELECT
    region,
    ROUND(AVG(cereal_yield_kg_ha), 1)   AS avg_cereal_yield_kg_ha,
    ROUND(AVG(fertilizer_kg_ha), 2)     AS avg_fertilizer_kg_ha,
    ROUND(AVG(food_production_index), 1) AS avg_food_production_index,
    ROUND(AVG(agr_value_added_pct), 2)  AS avg_agr_value_added_pct,
    ROUND(AVG(food_security_score), 2)  AS avg_food_security_score
FROM agriculture_metrics
GROUP BY region
ORDER BY avg_cereal_yield_kg_ha DESC;

-- ----------------------------------------------------------------------------
-- 7. Countries with a declining food production index (latest < earliest).
--    Flags negative long-run food-production trajectories (risk signal).
-- ----------------------------------------------------------------------------
WITH bounds AS (
    SELECT
        country_name,
        region,
        MIN(year) AS first_year,
        MAX(year) AS last_year
    FROM agriculture_metrics
    WHERE food_production_index IS NOT NULL
    GROUP BY country_name, region
)
SELECT
    b.country_name,
    b.region,
    early.food_production_index AS first_year_index,
    late.food_production_index  AS last_year_index,
    ROUND(late.food_production_index - early.food_production_index, 2)
        AS index_change
FROM bounds b
JOIN agriculture_metrics early
    ON early.country_name = b.country_name AND early.year = b.first_year
JOIN agriculture_metrics late
    ON late.country_name = b.country_name AND late.year = b.last_year
WHERE late.food_production_index < early.food_production_index
ORDER BY index_change ASC;

-- ----------------------------------------------------------------------------
-- 8. High rural population + low food security (correlation proxy).
--    Surfaces countries where large rural populations coincide with weak
--    food-security scores -- prime candidates for rural AI interventions.
-- ----------------------------------------------------------------------------
SELECT
    country_name,
    region,
    ROUND(AVG(rural_population_pct), 2) AS avg_rural_population_pct,
    ROUND(AVG(food_security_score), 2)  AS avg_food_security_score
FROM agriculture_metrics
WHERE rural_population_pct IS NOT NULL
  AND food_security_score IS NOT NULL
GROUP BY country_name, region
HAVING AVG(rural_population_pct) > 50
   AND AVG(food_security_score) < 50
ORDER BY avg_rural_population_pct DESC;

-- ----------------------------------------------------------------------------
-- 9. Top AI investment priority countries (Critical / High buckets).
--    Produces the prioritized target list for AI-driven agri investment.
-- ----------------------------------------------------------------------------
SELECT
    country_name,
    region,
    ai_investment_priority,
    avg_food_security_score,
    avg_cereal_yield_kg_ha,
    avg_yield_growth_rate
FROM country_summary
WHERE ai_investment_priority IN ('Critical', 'High')
ORDER BY
    CASE ai_investment_priority
        WHEN 'Critical' THEN 1
        WHEN 'High'     THEN 2
        ELSE 3
    END,
    avg_food_security_score ASC;

-- ----------------------------------------------------------------------------
-- 10. Annual food-production trend for the top and bottom yield countries.
--     Compares the yearly food-production trajectory of the highest- and
--     lowest-yielding African countries side by side.
-- ----------------------------------------------------------------------------
WITH ranked AS (
    SELECT
        country_name,
        AVG(cereal_yield_kg_ha) AS avg_yield,
        RANK() OVER (ORDER BY AVG(cereal_yield_kg_ha) DESC) AS rank_high,
        RANK() OVER (ORDER BY AVG(cereal_yield_kg_ha) ASC)  AS rank_low
    FROM agriculture_metrics
    WHERE region = 'Africa' AND cereal_yield_kg_ha IS NOT NULL
    GROUP BY country_name
),
extremes AS (
    SELECT country_name FROM ranked WHERE rank_high = 1 OR rank_low = 1
)
SELECT
    m.country_name,
    m.year,
    m.food_production_index,
    m.cereal_yield_kg_ha
FROM agriculture_metrics m
JOIN extremes e ON e.country_name = m.country_name
WHERE m.food_production_index IS NOT NULL
ORDER BY m.country_name, m.year;
