"""Rule-based parsing of natural-language job search queries."""

import re
from dataclasses import dataclass
from typing import List


@dataclass
class ParsedQuery:
    """Normalized role and locations extracted from a search query."""

    keywords: str
    locations: List[str]


class QueryParser:
    """Parse job roles and cities without external services."""

    LOCATION_ALIASES = {
        'bangalore': 'bangalore',
        'bengaluru': 'bangalore',
        'gurugram': 'gurgaon',
        'gurgaon': 'gurgaon',
        'mumbai': 'mumbai',
        'pune': 'pune',
        'delhi': 'delhi',
        'noida': 'noida',
        'hyderabad': 'hyderabad',
        'chennai': 'chennai',
        'india': 'india',
    }
    PREPOSITION_PATTERN = re.compile(r'\b(?:in|at|across)\b', re.IGNORECASE)
    LOCATION_SPLIT_PATTERN = re.compile(r'\s*(?:,|\band\b|\bor\b|&)\s*', re.IGNORECASE)
    ROLE_STOP_PATTERN = re.compile(
        r'\b(?:find|search|looking\s+for|jobs?|roles?|openings?)\b',
        re.IGNORECASE,
    )

    def parse(self, query: str) -> ParsedQuery:
        """Extract a normalized role and locations from natural language."""
        query = re.sub(r'\s+', ' ', (query or '').strip())
        if not query:
            return ParsedQuery('', [])

        match = self.PREPOSITION_PATTERN.search(query)
        if match:
            role_text = query[:match.start()]
            location_text = query[match.end():]
        else:
            role_text = query
            location_text = ''

        role_text = self.ROLE_STOP_PATTERN.sub(' ', role_text)
        role = re.sub(r'\s+', ' ', role_text).strip(' ,.-')
        locations = self._normalize_locations(location_text)
        return ParsedQuery(role, locations)

    def _normalize_locations(self, location_text: str) -> List[str]:
        locations = []
        for raw_location in self.LOCATION_SPLIT_PATTERN.split(location_text):
            normalized = re.sub(r'\s+', ' ', raw_location).strip(' .')
            if not normalized:
                continue
            normalized = self.LOCATION_ALIASES.get(
                normalized.lower(), normalized.lower()
            )
            if normalized not in locations:
                locations.append(normalized)
        return locations

    def build_naukri_urls(self, parsed_query: ParsedQuery) -> List[str]:
        """Build one Naukri search URL for each parsed city."""
        role_slug = self._slugify(parsed_query.keywords)
        if not role_slug:
            return []
        if not parsed_query.locations:
            return [f'https://www.naukri.com/{role_slug}-jobs']
        return [
            f'https://www.naukri.com/{role_slug}-jobs-in-{self._slugify(city)}'
            for city in parsed_query.locations
            if self._slugify(city)
        ]

    def role_slug(self, role: str) -> str:
        """Return a clean slug for an arbitrary job role."""
        return self._slugify(role)

    def location_slug(self, location: str) -> str:
        """Return a clean slug for a normalized city or location."""
        normalized = self.LOCATION_ALIASES.get(
            (location or '').strip().lower(), (location or '').strip().lower()
        )
        return self._slugify(normalized)

    @staticmethod
    def _slugify(value: str) -> str:
        slug = re.sub(r'[^a-z0-9]+', '-', (value or '').lower())
        return slug.strip('-')