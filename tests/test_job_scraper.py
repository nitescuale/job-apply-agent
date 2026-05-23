"""Tests pour backend/agents/job_scraper.py."""
import pytest

from backend.agents.job_scraper import scrape_job


JSONLD_HTML = """
<!doctype html>
<html><head>
<title>Senior Python Developer - ACME</title>
<meta property="og:description" content="Join our backend team.">
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "JobPosting",
  "title": "Senior Python Developer",
  "datePosted": "2026-04-01",
  "validThrough": "2026-06-01",
  "employmentType": "FULL_TIME",
  "hiringOrganization": {"@type": "Organization", "name": "ACME"},
  "jobLocation": {
    "@type": "Place",
    "address": {
      "@type": "PostalAddress",
      "addressLocality": "Paris",
      "addressCountry": "FR"
    }
  },
  "baseSalary": {
    "@type": "MonetaryAmount",
    "currency": "EUR",
    "value": {"@type": "QuantitativeValue", "minValue": 55000, "maxValue": 70000, "unitText": "YEAR"}
  },
  "jobLocationType": "TELECOMMUTE",
  "description": "<p>Build APIs with FastAPI</p>"
}
</script>
</head><body><h1>Senior Python Developer</h1><p>Build cool stuff.</p></body></html>
"""


def test_scrape_jsonld_extracts_all_fields():
    r = scrape_job(JSONLD_HTML, url="https://example.com/jobs/1")
    assert r["title"] == "Senior Python Developer"
    assert r["company"] == "ACME"
    assert "Paris" in r["location"]
    assert r["employment_type"] == "FULL_TIME"
    assert r["posted_date"] == "2026-04-01"
    assert r["valid_through"] == "2026-06-01"
    assert "55000" in r["salary"] and "70000" in r["salary"]
    assert r["remote"] is True
    assert "FastAPI" in r["description"]
    assert "<" not in r["description"]  # HTML stripped
    assert r["source"] == "json-ld"
    assert r["url"] == "https://example.com/jobs/1"


def test_scrape_falls_back_to_meta_when_no_jsonld():
    html = """
    <html><head>
      <title>Fallback Title</title>
      <meta property="og:title" content="Better Title">
      <meta property="og:description" content="Cool job">
    </head><body>content</body></html>
    """
    r = scrape_job(html, url="https://unknown-site.com/job")
    assert r["title"] == "Better Title"
    assert r["description"] == "Cool job"


def test_scrape_falls_back_to_title_tag_when_no_meta():
    html = "<html><head><title>Plain Title</title></head><body>text body content</body></html>"
    r = scrape_job(html, url="https://random.com/")
    assert r["title"] == "Plain Title"
    assert "text body content" in (r.get("description") or "")


def test_scrape_jsonld_inside_graph():
    html = """
    <html><head><script type="application/ld+json">
    {"@context": "https://schema.org", "@graph": [
      {"@type": "WebPage", "name": "ignore me"},
      {"@type": "JobPosting", "title": "Found via graph",
       "hiringOrganization": {"name": "GraphCo"}}
    ]}
    </script></head><body></body></html>
    """
    r = scrape_job(html, url="https://x.com/")
    assert r["title"] == "Found via graph"
    assert r["company"] == "GraphCo"


def test_scrape_empty_html_raises():
    with pytest.raises(ValueError):
        scrape_job("", url="https://x.com")


def test_scrape_whitespace_html_raises():
    with pytest.raises(ValueError):
        scrape_job("   \n\t  ", url="https://x.com")


def test_scrape_decodes_html_entities_in_description():
    """Le JSON-LD peut contenir une description HTML-encodée, parfois double-encodée."""
    html = """
    <html><head><script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "JobPosting",
      "title": "Dev",
      "description": "Allianz recherche un &lt;strong&gt;Data Scientist&lt;/strong&gt; pour le service R&amp;amp;D."
    }
    </script></head><body></body></html>
    """
    r = scrape_job(html, url="https://x.com/")
    desc = r["description"]
    assert "<" not in desc and ">" not in desc
    assert "&lt;" not in desc and "&amp;" not in desc
    assert "Data Scientist" in desc
    assert "R&D" in desc  # &amp;amp; → &amp; → &


def test_scrape_malformed_jsonld_does_not_crash():
    html = """
    <html><head>
      <title>Page Title</title>
      <script type="application/ld+json">{ this is not valid json }</script>
    </head><body>body</body></html>
    """
    r = scrape_job(html, url="https://x.com/")
    assert r["title"] == "Page Title"
