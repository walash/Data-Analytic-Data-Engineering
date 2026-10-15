-- ===========================================================================
-- HIV/AIDS Burden in Africa & the USA - analytical queries
-- Target database: data/hiv_aids.db (SQLite, built by src/loading/load.py)
-- Run:  sqlite3 -header -column data/hiv_aids.db < sql/queries.sql
--
-- Note: the World Bank publishes no HIV prevalence / ART / incidence / PLHIV
-- values for the United States, so US rows carry health expenditure only.
-- ===========================================================================


-- ---------------------------------------------------------------------------
-- Q1. Top countries by HIV prevalence (latest year with data)
-- Ranks countries by adult (15-49) HIV prevalence in their most recent year.
-- ---------------------------------------------------------------------------
WITH latest AS (
    SELECT country_code, MAX(year) AS year
    FROM hiv_metrics
    WHERE hiv_prevalence_pct IS NOT NULL
    GROUP BY country_code
)
SELECT
    RANK() OVER (ORDER BY m.hiv_prevalence_pct DESC) AS prevalence_rank,
    c.country_name,
    c.subregion,
    m.year,
    m.hiv_prevalence_pct
FROM hiv_metrics m
JOIN latest l ON l.country_code = m.country_code AND l.year = m.year
JOIN countries c ON c.country_code = m.country_code
ORDER BY prevalence_rank
LIMIT 10;


-- ---------------------------------------------------------------------------
-- Q2. ART coverage trends (2015 -> latest) per country
-- Shows how antiretroviral therapy coverage scaled up over the decade.
-- ---------------------------------------------------------------------------
SELECT
    c.country_name,
    MAX(CASE WHEN m.year = 2015 THEN m.art_coverage_pct END) AS art_2015,
    MAX(CASE WHEN m.year = 2018 THEN m.art_coverage_pct END) AS art_2018,
    MAX(CASE WHEN m.year = 2021 THEN m.art_coverage_pct END) AS art_2021,
    MAX(CASE WHEN m.year = 2024 THEN m.art_coverage_pct END) AS art_2024,
    MAX(CASE WHEN m.year = 2024 THEN m.art_coverage_pct END)
      - MAX(CASE WHEN m.year = 2015 THEN m.art_coverage_pct END) AS pp_gain_2015_2024
FROM hiv_metrics m
JOIN countries c ON c.country_code = m.country_code
WHERE m.art_coverage_pct IS NOT NULL
GROUP BY c.country_name
ORDER BY pp_gain_2015_2024 DESC;


-- ---------------------------------------------------------------------------
-- Q3. Treatment gap analysis (latest year)
-- Estimated people living with HIV who are NOT on ART
-- (treatment_gap = PLHIV - PLHIV x ART coverage %).
-- ---------------------------------------------------------------------------
SELECT
    c.country_name,
    m.year,
    CAST(m.plhiv_count AS INTEGER)    AS plhiv,
    m.art_coverage_pct,
    CAST(m.plhiv_on_art AS INTEGER)   AS est_on_art,
    CAST(m.treatment_gap AS INTEGER)  AS est_untreated,
    ROUND(100.0 * m.treatment_gap
          / SUM(m.treatment_gap) OVER (), 1) AS share_of_total_gap_pct
FROM hiv_metrics m
JOIN countries c ON c.country_code = m.country_code
WHERE m.year = (SELECT MAX(year) FROM hiv_metrics WHERE treatment_gap IS NOT NULL)
  AND m.treatment_gap IS NOT NULL
ORDER BY m.treatment_gap DESC;


-- ---------------------------------------------------------------------------
-- Q4. Africa vs USA comparison by year
-- Regional averages. HIV columns are NULL for the USA because the source
-- does not report them, while health expenditure is available for both.
-- ---------------------------------------------------------------------------
SELECT
    c.region,
    m.year,
    COUNT(DISTINCT m.country_code)              AS countries,
    ROUND(AVG(m.hiv_prevalence_pct), 2)         AS avg_hiv_prevalence_pct,
    ROUND(AVG(m.art_coverage_pct), 1)           AS avg_art_coverage_pct,
    ROUND(AVG(m.hiv_incidence_per1000), 2)      AS avg_incidence_per1000,
    CAST(SUM(m.plhiv_count) AS INTEGER)         AS total_plhiv,
    ROUND(AVG(m.health_exp_pct_gdp), 2)         AS avg_health_exp_pct_gdp
FROM hiv_metrics m
JOIN countries c ON c.country_code = m.country_code
WHERE m.year BETWEEN 2015 AND 2024
GROUP BY c.region, m.year
ORDER BY m.year, c.region;


-- ---------------------------------------------------------------------------
-- Q5. Year-over-year change in HIV incidence and prevalence
-- Uses LAG() to compute annual change and highlights the latest year's movers.
-- ---------------------------------------------------------------------------
WITH yoy AS (
    SELECT
        m.country_code,
        m.year,
        m.hiv_incidence_per1000,
        m.hiv_incidence_per1000 - LAG(m.hiv_incidence_per1000)
            OVER (PARTITION BY m.country_code ORDER BY m.year) AS incidence_change,
        m.hiv_prevalence_pct - LAG(m.hiv_prevalence_pct)
            OVER (PARTITION BY m.country_code ORDER BY m.year) AS prevalence_change
    FROM hiv_metrics m
)
SELECT
    c.country_name,
    y.year,
    y.hiv_incidence_per1000,
    ROUND(y.incidence_change, 3)  AS incidence_change_per1000,
    ROUND(y.prevalence_change, 2) AS prevalence_change_pp
FROM yoy y
JOIN countries c ON c.country_code = y.country_code
WHERE y.year = 2024 AND y.incidence_change IS NOT NULL
ORDER BY y.incidence_change ASC;


-- ---------------------------------------------------------------------------
-- Q6. Countries with the highest PLHIV burden (latest year)
-- Absolute number of people living with HIV and share of the 16-country total.
-- ---------------------------------------------------------------------------
WITH latest AS (
    SELECT m.country_code, m.year, m.plhiv_count
    FROM hiv_metrics m
    WHERE m.plhiv_count IS NOT NULL
      AND m.year = (SELECT MAX(year) FROM hiv_metrics m2
                    WHERE m2.country_code = m.country_code
                      AND m2.plhiv_count IS NOT NULL)
)
SELECT
    c.country_name,
    l.year,
    CAST(l.plhiv_count AS INTEGER) AS plhiv,
    ROUND(100.0 * l.plhiv_count / SUM(l.plhiv_count) OVER (), 1) AS share_pct,
    ROUND(100.0 * SUM(l.plhiv_count) OVER (ORDER BY l.plhiv_count DESC)
          / SUM(l.plhiv_count) OVER (), 1) AS cumulative_share_pct
FROM latest l
JOIN countries c ON c.country_code = l.country_code
ORDER BY l.plhiv_count DESC;


-- ---------------------------------------------------------------------------
-- Q7. Health expenditure vs HIV outcomes (correlation)
-- Pearson correlation between health spending (% GDP) and HIV prevalence /
-- ART coverage across African country-years, computed in pure SQL.
-- ---------------------------------------------------------------------------
WITH pairs AS (
    SELECT m.health_exp_pct_gdp AS x, m.hiv_prevalence_pct AS y, m.art_coverage_pct AS z
    FROM hiv_metrics m
    JOIN countries c ON c.country_code = m.country_code
    WHERE c.region = 'Africa'
      AND m.health_exp_pct_gdp IS NOT NULL
      AND m.hiv_prevalence_pct IS NOT NULL
      AND m.art_coverage_pct IS NOT NULL
),
stats AS (
    SELECT COUNT(*) AS n,
           AVG(x) AS mx, AVG(y) AS my, AVG(z) AS mz,
           AVG(x * x) AS mxx, AVG(y * y) AS myy, AVG(z * z) AS mzz,
           AVG(x * y) AS mxy, AVG(x * z) AS mxz
    FROM pairs
)
SELECT
    n AS country_years,
    ROUND((mxy - mx * my) / (SQRT(mxx - mx * mx) * SQRT(myy - my * my)), 3)
        AS corr_health_exp_vs_prevalence,
    ROUND((mxz - mx * mz) / (SQRT(mxx - mx * mx) * SQRT(mzz - mz * mz)), 3)
        AS corr_health_exp_vs_art_coverage
FROM stats;


-- ---------------------------------------------------------------------------
-- Q8. Countries achieving the UNAIDS 90-90-90 treatment target
-- 90% diagnosed x 90% of those on treatment = 81% of all PLHIV on ART.
-- Also flags the 95-95-95 equivalent (>= 90.25%) and first year achieved.
-- ---------------------------------------------------------------------------
SELECT
    c.country_name,
    MAX(CASE WHEN m.year = 2024 THEN m.art_coverage_pct END) AS art_coverage_2024,
    MIN(CASE WHEN m.meets_90_90_90_art = 1 THEN m.year END) AS first_year_90_90_90,
    MIN(CASE WHEN m.meets_95_95_95_art = 1 THEN m.year END) AS first_year_95_95_95,
    CASE
        WHEN MAX(CASE WHEN m.year = 2024 THEN m.meets_95_95_95_art END) = 1
            THEN 'Meets 95-95-95 treatment level'
        WHEN MAX(CASE WHEN m.year = 2024 THEN m.meets_90_90_90_art END) = 1
            THEN 'Meets 90-90-90 treatment level'
        ELSE 'Below 90-90-90 treatment level'
    END AS status_2024
FROM hiv_metrics m
JOIN countries c ON c.country_code = m.country_code
WHERE m.art_coverage_pct IS NOT NULL
GROUP BY c.country_name
ORDER BY art_coverage_2024 DESC;
