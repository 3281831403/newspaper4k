"""Extracts the publication date from article HTML."""

import re
from datetime import datetime

from dateutil.parser import parse as date_parser
from lxml.html import HtmlElement

import newspaper.parsers as parsers
from newspaper import urls
from newspaper.configuration import Configuration
from newspaper.extractors.defines import PUBLISH_DATE_META_INFO, PUBLISH_DATE_TAGS


class PubdateExtractor:
    """Extracts the publication date from article HTML."""

    def __init__(self, config: Configuration) -> None:
        """Initialize the PubdateExtractor.

        Args:
            config (Configuration): Configuration object controlling extraction behavior.
        """
        self.config = config
        self.pubdate: datetime | None = None

    def _parse_date_str(self, date_str: str | None) -> datetime | None:
        """Parse a date string into a datetime object.

        Args:
            date_str (str | None): The date string to parse.

        Returns:
            datetime | None: A datetime object, or None if parsing fails.
        """
        if not date_str:
            return None
        try:
            self.pubdate = date_parser(date_str)
            return self.pubdate
        except (ValueError, OverflowError, AttributeError, TypeError):
            # near all parse failures are due to URL dates without a day
            # specifier, e.g. /2014/04/
            return None

    def parse(self, article_url: str, doc: HtmlElement) -> datetime | None:
        """3 strategies for publishing date extraction.

        The strategies are descending in accuracy and the next strategy is only
        attempted if a preferred one fails.

        1. Pubdate from URL
        2. Pubdate from metadata
        3. Raw regex searches in the HTML + added heuristics
        """
        date_matches = []
        date_match = re.search(urls.STRICT_DATE_REGEX, article_url)
        if date_match:
            datetime_obj = self._parse_date_str(date_match.group(0))
            if datetime_obj:
                date_matches.append((datetime_obj, 10))  # date and matchscore

        # yoast seo structured data or json-ld
        json_ld_scripts = parsers.get_ld_json_object(doc)

        for script_tag in json_ld_scripts:
            if not isinstance(script_tag, dict):
                continue
            # Nodes inside a "@graph" keep the historical higher score (10);
            # standalone article nodes and nodes nested under "mainEntity"
            # keep the historical score of 9.
            graph_nodes = script_tag.get("@graph") if "@graph" in script_tag else None
            if isinstance(graph_nodes, list):
                article_nodes = [
                    item for item in graph_nodes if isinstance(item, dict) and item.get("datePublished") is not None
                ]
                score = 10
            else:
                article_nodes = [
                    item
                    for item in parsers.iter_json_ld_article_nodes([script_tag])
                    if item.get("datePublished") is not None
                ]
                score = 9

            for item in article_nodes:
                date_str = item.get("datePublished")
                datetime_obj = self._parse_date_str(date_str)
                if datetime_obj:
                    date_matches.append((datetime_obj, score))

            # Backwards compatible fallback: top level "dateCreated" on a
            # plain (non @graph) JSON-LD object. dateModified is never used
            # because the publication date must reflect datePublished.
            if graph_nodes is None and "dateCreated" in script_tag:
                datetime_obj = self._parse_date_str(script_tag["dateCreated"])
                if datetime_obj:
                    date_matches.append((datetime_obj, 7))

        # get <time> tags
        for item in parsers.get_tags(doc, tag="time"):
            if item.get("datetime"):
                datetime_obj = self._parse_date_str(item.get("datetime"))
                if datetime_obj:
                    if item.text and re.search("published|\bon:", item.text, re.I):
                        date_matches.append((datetime_obj, 8))  # Boost if it has the word published or on
                    else:
                        date_matches.append((datetime_obj, 5))
        candidates = []

        for known_meta_info in PUBLISH_DATE_META_INFO:
            candidates.extend(parsers.get_metatags(doc, value=known_meta_info))
        candidates = [(x, "content") for x in candidates]  # property that contains
        # the date
        # is always 'content'
        for known_meta_tag in PUBLISH_DATE_TAGS:
            candidates.extend(
                [
                    (x, known_meta_tag["content"])
                    for x in parsers.get_elements_by_attribs(
                        doc,
                        attribs={known_meta_tag["attribute"]: known_meta_tag["value"]},
                    )
                ]
            )

        for meta_tag, content_attr in candidates:
            datetime_obj = self._parse_date_str(parsers.get_attribute(meta_tag, content_attr))
            if datetime_obj:
                score = 6
                if meta_tag.tag.lower() == "meta":
                    score += 1  # Boost meta tags
                days_diff = (datetime.now().date() - datetime_obj.date()).days
                if days_diff < 0:  # articles from the future
                    score -= 2
                elif days_diff > 25 * 365:  # very old articles
                    score -= 1
                date_matches.append((datetime_obj, score))

        date_matches.sort(key=lambda x: x[1], reverse=True)
        self.pubdate = date_matches[0][0] if date_matches else None
        return self.pubdate
