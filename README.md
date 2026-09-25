# Job Agent

A multi-platform job scraping agent that aggregates job listings from Naukri, Wellfound, and RemoteOK, storing relevant job information in a structured CSV format for easy analysis and tracking.

## Features

- **Multi-platform aggregation**: Scrapes jobs from Naukri, Wellfound, and RemoteOK
- **Configurable search**: Filter by job titles, keywords, locations, and experience level
- **Data normalization**: Standardizes job data across different platforms
- **Deduplication**: Removes duplicate job listings across sources
- **CSV export**: Stores data in CSV format with consistent schema
- **Respectful scraping**: Implements rate limiting and respects platform policies
- **Error handling**: Comprehensive error handling and logging

## Tech Stack

- **Language**: Python 3.8+
- **Data Extraction**:
  - **Naukri**: HTML scraping using BeautifulSoup and requests
  - **RemoteOK**: Public API integration
  - **Wellfound**: Firecrawl for JavaScript-rendered content
- **Data Processing**: pandas for data manipulation
- **Storage**: CSV format
- **Configuration**: python-dotenv for environment management

## Project Structure

```
job-agent/
├── src/
│   ├── scrapers/
│   │   ├── naukri_scraper.py      # Naukri HTML scraper
│   │   ├── remoteok_scraper.py    # RemoteOK API scraper
│   │   └── wellfound_scraper.py   # Wellfound Firecrawl scraper
│   ├── processors/
│   │   ├── data_normalizer.py     # Data normalization
│   │   └── deduplicator.py        # Duplicate removal
│   ├── storage/
│   │   └── csv_writer.py          # CSV file operations
│   ├── config/
│   │   ├── settings.py            # Configuration management
│   │   └── logging_config.py      # Logging setup
│   └── main.py                    # Main application entry point
├── tests/                         # Test files
├── data/                          # Scraped job data (CSV files)
├── docs/                          # Documentation
├── logs/                          # Application logs
├── requirements.txt               # Python dependencies
├── .env.example                   # Environment variables template
└── README.md                      # This file
```

## Installation

1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd job-agent
   ```

2. **Create a virtual environment**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**:
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

## Configuration

Edit the `.env` file to configure the application:

### Required Configuration

```bash
# Firecrawl API Key (required for Wellfound scraper)
FIRECRAWL_API_KEY=your_firecrawl_api_key_here
```

### Optional Configuration

```bash
# Search Configuration
SEARCH_KEYWORDS=python,developer,software engineer
JOB_TITLES=Senior Python Developer,Software Engineer,Full Stack Developer

# Location Filters
LOCATIONS=remote,Bangalore,Mumbai,Delhi
COUNTRY_FILTER=India

# Experience Level
EXPERIENCE_LEVEL=mid-level,senior

# Rate Limiting
REQUEST_DELAY=2  # seconds between requests
MAX_RETRIES=3
TIMEOUT=30  # seconds

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/job_agent.log

# Data Storage
DATA_DIR=data
CSV_FILENAME_PATTERN=jobs_{date}.csv
```

## Usage

### Run the Job Agent

```bash
python src/main.py
```

This will:
1. Scrape jobs from all configured platforms
2. Normalize the data
3. Remove duplicates
4. Save results to a CSV file in the `data/` directory

### Output

Jobs are saved as CSV files with the following structure:

| title | company | location | description | salary | url | posting_date | job_type | source | scraped_at |
|-------|---------|----------|-------------|---------|-----|--------------|----------|--------|------------|

## Data Fields

For each job, the following information is captured:

- **Title**: Job title
- **Company**: Company name
- **Location**: Job location (city, country, or remote)
- **Description**: Job description summary
- **Salary**: Salary range (if available)
- **URL**: Link to the original job posting
- **Posting Date**: When the job was posted
- **Job Type**: Full-time, contract, etc.
- **Source**: Platform where the job was found (naukri, remoteok, wellfound)
- **Scraped At**: Timestamp when the job was scraped

## Development

### Running Tests

```bash
pytest tests/
```

### Adding New Scrapers

1. Create a new scraper file in `src/scrapers/`
2. Implement the scraper class following the existing pattern
3. Add the scraper to `src/scrapers/__init__.py`
4. Integrate in `src/main.py`

### Extending Data Processing

Add new processors in `src/processors/` following the existing patterns for normalization and deduplication.

## Ethical Considerations

This project is designed for educational and personal use. Please:

- Respect robots.txt and platform terms of service
- Implement rate limiting to avoid overwhelming servers
- Use official APIs when available
- Consider user-agent identification
- Handle errors gracefully

## Troubleshooting

### Firecrawl API Issues

If the Wellfound scraper doesn't work:
- Ensure your `FIRECRAWL_API_KEY` is set correctly in `.env`
- Check that your Firecrawl API subscription is active
- Verify you have sufficient API credits

### Naukri Scraping Issues

If Naukri scraping fails:
- Check your internet connection
- Verify the user-agent configuration
- Naukri may have updated their HTML structure

### Rate Limiting

If you encounter rate limiting:
- Increase `REQUEST_DELAY` in `.env`
- Reduce the number of search keywords
- Consider running scrapers at different times

## Roadmap

- [ ] Phase 2: RemoteOK API Integration
- [ ] Phase 3: Naukri HTML Scraper
- [ ] Phase 4: Wellfound Firecrawl Integration
- [ ] Phase 5: Data Processing Pipeline
- [ ] Phase 6: CSV Storage Layer
- [ ] Phase 7: Main Orchestration
- [ ] Phase 8: Documentation & Deployment
- [ ] Phase 9: Future Enhancements

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is for educational purposes. Please respect the terms of service of the job boards being scraped.

## Disclaimer

This tool is for personal and educational use only. Users are responsible for ensuring their use complies with the terms of service of the respective job boards and applicable laws and regulations.
---
*Maintained by [harshyadav-ml](https://github.com/harshyadav-ml)*
