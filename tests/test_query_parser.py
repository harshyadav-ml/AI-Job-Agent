"""Tests for natural-language job query parsing."""

from src.processors.query_parser import ParsedQuery, QueryParser


def test_parse_role_and_single_city():
    parsed = QueryParser().parse("find product manager roles in bangalore")

    assert parsed == ParsedQuery("product manager", ["bangalore"])


def test_parse_multiple_cities_and_aliases():
    parsed = QueryParser().parse(
        "software engineer jobs in pune, mumbai and bengaluru"
    )

    assert parsed.keywords == "software engineer"
    assert parsed.locations == ["pune", "mumbai", "bangalore"]


def test_build_naukri_urls():
    urls = QueryParser().build_naukri_urls(
        ParsedQuery("product manager", ["bangalore"])
    )

    assert urls == [
        "https://www.naukri.com/product-manager-jobs-in-bangalore"
    ]


def test_dynamic_role_and_location_slugs():
    parser = QueryParser()

    assert parser.role_slug("AI Engineer") == "ai-engineer"
    assert parser.role_slug("frontend developer") == "frontend-developer"
    assert parser.location_slug("Bengaluru") == "bangalore"
    assert parser.location_slug("New Delhi") == "new-delhi"