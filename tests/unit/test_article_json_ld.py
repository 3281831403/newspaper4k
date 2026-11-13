# ruff: noqa: D100, D103
"""End-to-end Article.parse() tests for JSON-LD only news pages."""

import json

from newspaper import Article


def _parse(html: str) -> Article:
    article = Article("https://example.com/article", fetch_images=False)
    article.download(input_html=html)
    article.parse()
    return article


def test_article_populates_fields_from_comment_wrapped_json_ld():
    html = """
    <html><head>
    <script type="application/ld+json"><!--
    {"@context": "https://schema.org", "@type": "NewsArticle",
     "headline": "Comment wrapped",
     "datePublished": "2024-05-01T10:00:00Z",
     "dateModified": "2024-06-01T10:00:00Z",
     "author": {"@type": "Person", "name": "Jane Doe"},
     "publisher": {"@type": "Organization", "name": "Example News"},
     "description": "JSON-LD only description",
     "mainEntityOfPage": "https://example.com/article-1"}
    --></script>
    </head><body><p>Body text</p></body></html>
    """

    article = _parse(html)

    assert article.meta_site_name == "Example News"
    assert article.meta_description == "JSON-LD only description"
    assert article.canonical_link == "https://example.com/article-1"
    assert article.authors == ["Jane Doe"]
    assert article.publish_date is not None
    assert article.publish_date.isoformat().startswith("2024-05-01T10:00:00")


def test_article_populates_fields_from_yoast_graph():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@graph": [
      {"@type": "WebSite", "name": "Example Site", "url": "https://example.com"},
      {"@type": "NewsArticle", "headline": "Graph article",
       "datePublished": "2024-05-02T09:00:00Z",
       "dateModified": "2024-07-02T09:00:00Z",
       "author": {"@type": "Person", "name": "John Smith"},
       "description": "Graph description",
       "mainEntityOfPage": {"@type": "WebPage",
         "@id": "https://example.com/article-2"}},
      {"@type": "Person", "name": "John Smith"}
    ]}
    </script>
    </head><body><p>Body</p></body></html>
    """

    article = _parse(html)

    assert article.meta_site_name == "Example Site"
    assert article.meta_description == "Graph description"
    assert article.canonical_link == "https://example.com/article-2"
    assert article.authors == ["John Smith"]
    assert article.publish_date is not None
    assert article.publish_date.isoformat().startswith("2024-05-02T09:00:00")


def test_article_populates_fields_from_main_entity_node():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "WebPage",
     "name": "Example Site",
     "url": "https://example.com",
     "mainEntity": {"@type": "NewsArticle", "headline": "Nested article",
       "datePublished": "2024-05-03T08:00:00Z",
       "dateModified": "2024-08-03T08:00:00Z",
       "author": [{"@type": "Person", "name": "Alice"}],
       "description": "Main entity description",
       "mainEntityOfPage": {"@type": "WebPage",
         "@id": "https://example.com/article-3"}}}
    </script>
    </head><body><p>Body</p></body></html>
    """

    article = _parse(html)

    assert article.meta_site_name == "Example Site"
    assert article.meta_description == "Main entity description"
    assert article.canonical_link == "https://example.com/article-3"
    assert article.authors == ["Alice"]
    assert article.publish_date is not None
    assert article.publish_date.isoformat().startswith("2024-05-03T08:00:00")


def test_to_json_contains_json_ld_fallback_fields():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type": "NewsArticle",
     "datePublished": "2023-01-15T08:00:00Z",
     "dateModified": "2025-06-01T08:00:00Z",
     "author": [{"name": "Bob"}],
     "description": "DD",
     "publisher": {"name": "Site"},
     "mainEntityOfPage": {"@id": "https://example.com/canon"}}
    </script>
    </head><body><p>x</p></body></html>
    """

    payload = json.loads(_parse(html).to_json())

    assert payload["meta_site_name"] == "Site"
    assert payload["meta_description"] == "DD"
    assert payload["canonical_link"] == "https://example.com/canon"
    assert payload["authors"] == ["Bob"]
    assert payload["publish_date"].startswith("2023-01-15T08:00:00")


def test_head_meta_values_are_not_overridden_by_json_ld():
    html = """
    <html><head>
      <meta property="og:site_name" content="META SITE">
      <meta name="description" content="META DESC">
      <link rel="canonical" href="https://example.com/from-canonical">
      <meta name="author" content="Meta Author">
      <script type="application/ld+json">
        {"@type": "NewsArticle", "description": "LD DESC",
         "publisher": {"name": "LD SITE"},
         "mainEntityOfPage": "https://example.com/from-ld",
         "author": {"name": "LD Author"}}
      </script>
    </head><body><p>x</p></body></html>
    """

    article = _parse(html)

    assert article.meta_site_name == "META SITE"
    assert article.meta_description == "META DESC"
    assert article.canonical_link == "https://example.com/from-canonical"
    assert "Meta Author" in article.authors
