# Job Agent Project Context

## Project Overview
A multi-platform job aggregation engine that crawls and extracts structured job postings from Naukri, Wellfound, and RemoteOK. Extracted roles are normalized, deduplicated across platforms, and exported into timestamped CSV datasets for downstream analysis, search, and automated application tracking.

## Problem Solved
Job seekers must monitor multiple fragmented job boards simultaneously. This tool provides:
- **Multi-Source Aggregation**: Simultaneous scraping across major Indian job boards, remote platforms, and early-stage tech/startup platforms.
- **Bot-Mitigation Bypass**: Execution strategies tailored to bypass aggressive edge security (Akamai WAF, Cloudflare).
- **Automated Data Processing**: Normalization of titles, companies, locations, and compensation into a unified schema.
- **Zero-Dependency CLI Parsing**: Natural-language query extraction mapping directly to target search URLs.

## Data Sources & Extraction Architecture

| Platform | Extraction Vector | Mechanism & Anti-Bot Strategy | Source Type |
|---|---|---|---|
| **Naukri** | undetected-chromedriver + BeautifulSoup | Patches system Chrome binary to bypass Akamai EdgeSuite WAF; extracts from #listContainer | Domestic (India) |
| **RemoteOK** | Public REST API (https://remoteok.com/api) | Direct HTTP consumption via requests.Session with tokenized role & domain filtering | Global / Remote |
| **Wellfound** | Firecrawl API (V1FirecrawlApp) | Managed residential proxy pool rendering dynamic JS into structured Markdown and HTML | Startups & Tech |

## Unified Data Model (Job)
All scrapers yield instances conforming to src.models.Job:
- title (str): Normalized job title
- company (str): Employer or hiring organization
- location (str): City, Region, or "Remote"
- description (str): Sanitized summary/snippet
- salary (str): Normalized pay range or "Not specified"
- url (str): Canonical posting destination URL
- posted_date (str): ISO formatted date (YYYY-MM-DD) or relative text
- job_type (str): Categorized classification (Full-time, Internship, Contract, Part-time)
- source (str): Platform identifier (naukri, remoteok, wellfound)
- scraped_at (str): ISO timestamp of scrape execution

## Rule-Based Query Parser & URL Generation
Natural language search strings are parsed via standard library regex in src/processors/query_parser.py:
- Preposition separation (in, at, across) isolates the target role from geographic locations.
- Geographic aliases are normalized (bengaluru -> bangalore, gurugram -> gurgaon).
- Role aliases are expanded across all supported sources. For example, a query such as `FInd Project manager in banglore` should search for `PM` roles on Naukri, RemoteOK, and Wellfound, while normalizing the location to Bangalore.
- Generates platform-specific URL slugs:
  - Naukri: https://www.naukri.com/{role-slug}-jobs-in-{city-slug}
  - Wellfound: https://wellfound.com/role/l/{role-slug}/{city-slug}

Execution signature:
python3 -m src.main --query "find machine learning intern roles in bangalore"

## Storage & Output Convention
Datasets are stored in the project's data/ directory using search-specific slugs and timestamps to prevent collisions:
data/jobs_{role-slug}_{location-slug}_{YYYYMMDD_HHMMSS}.csv

## Current Implementation Status
- [x] Phase 1: Project Foundation & Environment
- [x] Phase 2: RemoteOK API Integration
- [x] Phase 3: Naukri Scraper Integration (undetected-chromedriver)
- [x] Phase 4: Wellfound Firecrawl Scraper
- [x] Phase 5: Data Normalization & Deduplication
- [x] Phase 6: Search-Specific CSV Writer
- [x] Phase 7: Main Pipeline Orchestration
- [x] Phase 8: Test Suite Validation (All tests passing)

## Active Road Ahead (Phase 9)
- Local Embedding Matching: Integrating sentence-transformers for scoring listings against candidate resume profiles.
- Headless Profile Persistence: Maintaining persistent browser sessions to eliminate headful popups during automated background cron jobs.
- Application Automation: Webhook dispatch for auto-notifying and drafting tailored application cover letters.
