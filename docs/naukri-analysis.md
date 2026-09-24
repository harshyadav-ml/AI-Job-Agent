# Naukri.com Scraping Analysis

## Website Structure Analysis

### Job Card Container
- **Main Container**: `div.srp-jobtuple-wrapper`
- **Alternative**: Individual job cards with nested row divs (row1, row2, row3, etc.)

### CSS Selectors (Based on Research)

| Field | CSS Selector | Notes |
|-------|--------------|-------|
| Job Title | `a.title` or `div.row1 a` | Primary title element |
| Company | `a.comp-name` or `div.row2 span a` | Company name link |
| Experience | `span.exp-wrap` | Experience requirement |
| Salary | `span.sal-wrap` | Salary information |
| Location | `span.loc-wrap` | Job location |
| Skills | `ul.tags-gt` | Required skills list |
| Posting Date | `span.job-post-day` | When job was posted |
| Job URL | `a.title` href attribute | Link to job details |

### URL Structure
- **Base URL**: `https://www.naukri.com`
- **Search Pattern**: `https://www.naukri.com/{keyword}-jobs`
- **With Location**: `https://www.naukri.com/{keyword}-jobs-in-{location}`

## Challenges & Anti-Scraping Measures

### 1. JavaScript Rendering
- **Issue**: Naukri uses client-side JavaScript for dynamic content
- **Impact**: Static HTML requests may not get complete job data
- **Mitigation**: Use proper headers, handle partial data gracefully

### 2. Class Name Changes
- **Issue**: Naukri developers frequently change CSS class names
- **Impact**: Selectors may break over time
- **Mitigation**: Implement fallback selectors, robust error handling

### 3. Rate Limiting
- **Issue**: Aggressive scraping can trigger IP blocks
- **Impact**: Requests may be blocked or return CAPTCHA
- **Mitigation**: Implement rate limiting (2-3 seconds between requests), use random delays

### 4. Session Management
- **Issue**: Some features require session cookies
- **Impact**: Missing data or blocked requests
- **Mitigation**: Use requests.Session for cookie management

## Implementation Strategy

### Approach: BeautifulSoup + Requests
Since our architecture specifies BeautifulSoup and requests (not Selenium), we'll:

1. **Use Requests with Proper Headers**
   - Realistic user-agent string
   - Accept headers
   - Connection management

2. **Implement Robust Error Handling**
   - Fallback selectors for each field
   - Graceful handling of missing data
   - Retry logic for failed requests

3. **Rate Limiting**
   - Configurable delays between requests
   - Random jitter to avoid detection
   - Respect robots.txt

4. **Session Management**
   - Use requests.Session for cookies
   - Handle session persistence
   - Manage connection pooling

### Fallback Selector Strategy

For each field, implement multiple selector attempts:
```python
def get_title(job_card):
    selectors = [
        'a.title',
        'div.row1 a',
        'h2 a',
        '.job-title a'
    ]
    for selector in selectors:
        element = job_card.select_one(selector)
        if element:
            return element.get_text(strip=True)
    return ""
```

## Data Quality Considerations

### Missing Fields
- Salary information often not available
- Posting dates may be relative ("2 days ago")
- Location data may be inconsistent

### Data Cleaning
- Remove whitespace and special characters
- Standardize location formats
- Parse relative dates to absolute dates
- Clean salary ranges

## Testing Strategy

### Unit Tests
- Test individual field extraction
- Test fallback selector logic
- Test data cleaning functions

### Integration Tests
- Test with live Naukri pages
- Test pagination handling
- Test error scenarios

### Performance Tests
- Test rate limiting effectiveness
- Test memory usage with large datasets
- Test concurrent request handling

## Legal & Ethical Considerations

### robots.txt
- Check and respect robots.txt
- Implement polite scraping intervals

### Terms of Service
- Review Naukri's terms of service
- Ensure compliance with usage policies

### Attribution
- Provide attribution to Naukri as data source
- Do not republish data without permission