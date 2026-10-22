-- DuckDB SQL query for analyzing scraped job postings.
-- Note: 'header=true' is explicitly specified so DuckDB always treats the first row
-- as column headers rather than inferring generic 'column 1', 'column 2' names.

SELECT 
    title,
    company,
    location,
    salary,
    job_type,
    source,
    posted_date,
    scraped_at
FROM read_csv('data/jobs_software_banglore_20261008_080702.csv', header=true)
WHERE title IS NOT NULL
ORDER BY scraped_at DESC;
