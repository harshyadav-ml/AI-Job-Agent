# RemoteOK API Documentation

## API Endpoint
- **URL**: `https://remoteok.com/api`
- **Method**: GET
- **Authentication**: None required (public API)
- **Rate Limiting**: No explicit rate limit documented, but respectful usage recommended

## Response Structure

### Format
- Returns a JSON array
- First element: API metadata (legal terms, last_updated timestamp)
- Subsequent elements: Job listings

### Job Object Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| slug | string | URL-friendly job identifier | "remote-junior-digital-assets-operations-analyst-omega-enterprises-1137418" |
| id | string | Unique job ID | "1137418" |
| epoch | number | Unix timestamp of job posting | 1790087814 |
| date | string | ISO 8601 datetime | "2026-09-22T14:36:54+00:00" |
| company | string | Company name | "Omega Enterprises" |
| company_logo | string | URL to company logo | "" (empty if not available) |
| position | string | Job title | "Junior Digital Assets Operations Analyst" |
| tags | array | Job tags/skills | ["crypto", "remote", "python", "junior"] |
| description | string | HTML job description | "<p>Application URL...</p>" |
| location | string | Job location | "" (empty for remote jobs) |
| apply_url | string | Application URL | "https://remoteOK.com/remote-jobs/..." |
| salary_min | number | Minimum salary | 0 (if not specified) |
| salary_max | number | Maximum salary | 0 (if not specified) |
| logo | string | Company logo URL | "" (empty if not available) |
| url | string | Job listing URL | "https://remoteOK.com/remote-jobs/..." |

## API Terms of Service
- Must link back to RemoteOK with follow link
- Must mention Remote OK as source
- Cannot use Remote OK logo without written permission
- Failure to comply may result in API access suspension

## Search/Filter Capabilities
- The main API endpoint returns all active jobs
- Filtering should be done client-side by:
  - Keywords in position/title
  - Tags array
  - Location (though most are remote)
  - Date range

## Rate Limiting Considerations
- No official rate limit documented
- Recommend: 1 request per minute maximum
- Implement request delays between API calls
- Cache responses when possible

## Data Quality Notes
- Salary information often not available (0, 0)
- Location field often empty for remote jobs
- Description contains HTML markup
- Tags are useful for filtering by skills/stack
- Date field is reliable for sorting