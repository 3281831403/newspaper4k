# ruff: noqa: D100, D103
"""Tests for JSON-LD parsing helpers in newspaper.parsers.

These cover the layouts found on modern news pages: payloads wrapped in
HTML comments, Yoast style ``@graph`` arrays and articles nested under
``mainEntity``.
"""

import lxml.html

import newspaper.parsers as parsers
from newspaper.configuration import Configuration
from newspaper.extractors.authors_extractor import AuthorsExtractor
from newspaper.extractors.metadata_extractor import MetadataExtractor
from newspaper.extractors.pubdate_extractor import PubdateExtractor


def _doc(script_body: str) -> lxml.html.HtmlElement:
    return lxml.html.fromstring(
        f'<html><head><script type="application/ld+json">{script_body}</script></head><body></body></html>'
    )


def test_get_ld_json_object_unwraps_html_comments():
    doc = _doc('<!--\n{"@type": "NewsArticle", "headline": "H"}\n-->')

    assert parsers.get_ld_json_object(doc) == [{"@type": "NewsArticle", "headline": "H"}]


def test_get_ld_json_object_flattens_arrays_and_keeps_dicts():
    doc = _doc('[{"@type": "NewsArticle"}, null, "ignored"]')

    assert parsers.get_ld_json_object(doc) == [{"@type": "NewsArticle"}]


def test_get_ld_json_object_ignores_invalid_payloads():
    doc = lxml.html.fromstring(
        '<html><head><script type="application/ld+json">not json</script>'
        '<script type="application/ld+json">{"@type": "Article"}</script>'
        "</head></html>"
    )

    assert parsers.get_ld_json_object(doc) == [{"@type": "Article"}]


def test_iter_json_ld_nodes_traverses_graph_and_main_entity():
    scripts = [
        {
            "@context": "https://schema.org",
            "@graph": [
                {"@type": "WebSite", "name": "Site"},
                {
                    "@type": "WebPage",
                    "mainEntity": {"@type": "NewsArticle", "headline": "Nested"},
                },
            ],
        }
    ]

    types = [node.get("@type") for node in parsers.iter_json_ld_nodes(scripts)]

    assert types == [
        None,  # root node has no @type of its own
        "WebSite",
        "WebPage",
        "NewsArticle",
    ]


def test_iter_json_ld_article_nodes_skips_non_article_nodes():
    scripts = [
        {
            "@graph": [
                {"@type": "WebSite", "name": "Site"},
                {"@type": "Person", "name": "Jane"},
                {"@type": "NewsArticle", "headline": "Article"},
            ]
        }
    ]

    nodes = list(parsers.iter_json_ld_article_nodes(scripts))

    assert len(nodes) == 1
    assert nodes[0]["headline"] == "Article"


def test_iter_json_ld_article_nodes_finds_main_entity_article():
    scripts = [
        {
            "@type": "WebPage",
            "mainEntity": {"@type": "BlogPosting", "headline": "Post"},
        }
    ]

    nodes = list(parsers.iter_json_ld_article_nodes(scripts))

    assert [node["headline"] for node in nodes] == ["Post"]


def test_iter_json_ld_article_nodes_accepts_list_type():
    scripts = [{"@type": ["WebPage", "NewsArticle"], "headline": "Multi"}]

    nodes = list(parsers.iter_json_ld_article_nodes(scripts))

    assert nodes[0]["headline"] == "Multi"


def test_pubdate_uses_date_published_inside_main_entity():
    extractor = PubdateExtractor(Configuration())
    doc = _doc(
        '{"@type": "WebPage", "mainEntity": {"@type": "NewsArticle",'
        '"datePublished": "2023-01-15T08:00:00Z",'
        '"dateModified": "2025-06-01T08:00:00Z"}}'
    )

    pubdate = extractor.parse("https://example.com/story", doc)

    assert pubdate is not None
    assert pubdate.isoformat().startswith("2023-01-15T08:00:00")


def test_pubdate_never_uses_date_modified():
    extractor = PubdateExtractor(Configuration())
    doc = _doc('{"@type": "NewsArticle", "dateModified": "2019-09-09T09:09:09Z"}')

    assert extractor.parse("https://example.com/story", doc) is None


def test_pubdate_reads_comment_wrapped_payload():
    extractor = PubdateExtractor(Configuration())
    doc = _doc('<!-- {"@type": "NewsArticle", "datePublished": "2020-03-03T03:03:03Z"} -->')

    pubdate = extractor.parse("https://example.com/story", doc)

    assert pubdate is not None
    assert pubdate.isoformat().startswith("2020-03-03T03:03:03")


def test_authors_are_extracted_from_main_entity():
    extractor = AuthorsExtractor(Configuration())
    doc = _doc(
        '{"@type": "WebPage", "mainEntity": {"@type": "NewsArticle",'
        '"author": [{"@type": "Person", "name": "Alice"}, {"name": "Bob"}]}}'
    )

    assert extractor.parse(doc) == ["Alice", "Bob"]


def test_authors_are_extracted_from_comment_wrapped_graph():
    extractor = AuthorsExtractor(Configuration())
    doc = _doc(
        '<!-- {"@graph": [{"@type": "Person", "name": "Jane"},'
        '{"@type": "NewsArticle", "author": {"name": "John"}}]} -->'
    )

    assert sorted(extractor.parse(doc)) == ["Jane", "John"]


def test_metadata_falls_back_to_json_ld_when_meta_tags_are_absent():
    extractor = MetadataExtractor(Configuration())
    doc = _doc(
        '{"@graph": ['
        '{"@type": "WebSite", "name": "Example Site"},'
        '{"@type": "NewsArticle", "description": "Graph description",'
        '"publisher": {"@type": "Organization", "name": "Example News"},'
        '"mainEntityOfPage": {"@type": "WebPage",'
        '"@id": "https://example.com/article-2"}}]}'
    )

    metadata = extractor.parse("https://example.com/article-2", doc)

    assert metadata["site_name"] == "Example News"
    assert metadata["description"] == "Graph description"
    assert metadata["canonical_link"] == "https://example.com/article-2"


def test_metadata_reads_main_entity_and_comment_wrapped_payload():
    extractor = MetadataExtractor(Configuration())
    doc = _doc(
        '<!-- {"@type": "WebPage", "name": "Example Site",'
        '"mainEntity": {"@type": "NewsArticle", "description": "Main description",'
        '"mainEntityOfPage": "https://example.com/article-3"}} -->'
    )

    metadata = extractor.parse("https://example.com/article-3", doc)

    assert metadata["site_name"] == "Example Site"
    assert metadata["description"] == "Main description"
    assert metadata["canonical_link"] == "https://example.com/article-3"


def test_metadata_meta_tags_take_precedence_over_json_ld():
    extractor = MetadataExtractor(Configuration())
    doc = lxml.html.fromstring(
        """
        <html><head>
          <meta property="og:site_name" content="META SITE">
          <meta name="description" content="META DESC">
          <link rel="canonical" href="https://example.com/from-canonical">
          <script type="application/ld+json">
            {"@type": "NewsArticle", "description": "LD DESC",
             "publisher": {"name": "LD SITE"},
             "mainEntityOfPage": "https://example.com/from-ld"}
          </script>
        </head></html>
        """
    )

    metadata = extractor.parse("https://example.com/from-canonical", doc)

    assert metadata["site_name"] == "META SITE"
    assert metadata["description"] == "META DESC"
    assert metadata["canonical_link"] == "https://example.com/from-canonical"


def test_metadata_canonical_resolves_relative_json_ld_url():
    extractor = MetadataExtractor(Configuration())
    doc = _doc('{"@type": "NewsArticle", "url": "/relative/article"}')

    metadata = extractor.parse("https://example.com/section/page", doc)

    assert metadata["canonical_link"] == "https://example.com/relative/article"


def test_metadata_without_json_ld_keeps_empty_values():
    extractor = MetadataExtractor(Configuration())
    doc = lxml.html.fromstring("<html></html>")

    metadata = extractor.parse("https://example.com/story", doc)

    assert metadata["site_name"] == ""
    assert metadata["description"] == ""
    assert metadata["canonical_link"] is None
