"""Extracts metadata (language, canonical URL, og tags, etc.) from article HTML."""

import re
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from lxml.html import HtmlElement

import newspaper.parsers as parsers
from newspaper.configuration import Configuration
from newspaper.extractors.defines import A_HREF_TAG_SELECTOR, A_REL_TAG_SELECTOR, META_LANGUAGE_TAGS, RE_LANG
from newspaper.languages import ISO639_3_TO_1


class MetadataExtractor:
    """Extracts metadata (language, canonical URL, og tags, etc.) from article HTML."""

    def __init__(self, config: Configuration) -> None:
        """Initialize the MetadataExtractor.

        Args:
            config (Configuration): Configuration object controlling extraction behavior.
        """
        self.config = config
        self.meta_data: dict[str, Any] = {
            "language": None,
            "type": None,
            "canonical_link": None,
            "site_name": None,
            "description": None,
            "keywords": None,
            "tags": None,
            "data": None,
        }

    def parse(self, article_url: str, doc: HtmlElement) -> dict[str, Any]:
        """Parse the article's HTML for any known metadata attributes"""
        self.meta_data["language"] = self._get_meta_language(doc)
        self.meta_data["type"] = self._get_meta_field(doc, "og:type")
        self.meta_data["canonical_link"] = self._get_canonical_link(article_url, doc)
        self.meta_data["site_name"] = self._get_meta_field(doc, "og:site_name")
        self.meta_data["description"] = self._get_meta_field(doc, ["description", "og:description"])

        # JSON-LD is only a fallback: when the head already provides an
        # og/meta value it must not override it, so existing results for
        # pages with meta tags are unchanged.
        json_ld_fallback = self._get_json_ld_fallback(article_url, doc)
        if not self.meta_data["canonical_link"]:
            self.meta_data["canonical_link"] = json_ld_fallback["canonical_link"]
        if not self.meta_data["site_name"]:
            self.meta_data["site_name"] = json_ld_fallback["site_name"] or ""
        if not self.meta_data["description"]:
            self.meta_data["description"] = json_ld_fallback["description"] or ""

        self.meta_data["keywords"] = [k.strip() for k in self._get_meta_field(doc, "keywords").split(",")]
        self.meta_data["data"] = self._get_metadata(doc)

        return self.meta_data

    def _get_json_ld_fallback(self, article_url: str, doc: HtmlElement) -> dict[str, str | None]:
        """Extract site_name, description and canonical link from JSON-LD.

        Used as a fallback for pages that only expose structured data
        (including Yoast ``@graph`` payloads and articles nested under
        ``mainEntity``). Fields that cannot be found stay ``None`` and the
        caller normalizes string fields to empty strings.
        """
        fallback: dict[str, str | None] = {
            "site_name": None,
            "description": None,
            "canonical_link": None,
        }
        json_ld_scripts = parsers.get_ld_json_object(doc)
        if not json_ld_scripts:
            return fallback

        article_nodes = list(parsers.iter_json_ld_article_nodes(json_ld_scripts))

        for node in article_nodes:
            if fallback["description"] is None:
                description = node.get("description")
                if isinstance(description, str) and description.strip():
                    fallback["description"] = description.strip()
            if fallback["canonical_link"] is None:
                fallback["canonical_link"] = self._get_canonical_from_json_ld(node, article_url)

        # Site name: prefer the article's publisher/name, then a WebSite
        # node living next to it (typical Yoast @graph layout).
        for node in article_nodes:
            name = self._get_publisher_name(node.get("publisher"))
            if name:
                fallback["site_name"] = name
                break

        if fallback["site_name"] is None:
            for node in parsers.iter_json_ld_nodes(json_ld_scripts):
                if node.get("@type") in ("WebSite", "WebPage", "NewsMediaOrganization"):
                    name = node.get("name")
                    if isinstance(name, str) and name.strip():
                        fallback["site_name"] = name.strip()
                        break

        return fallback

    @staticmethod
    def _get_publisher_name(publisher: Any) -> str | None:
        """Return the name of a JSON-LD publisher field if it is a string."""
        if isinstance(publisher, dict):
            name = publisher.get("name")
        elif isinstance(publisher, list):
            name = next((item.get("name") for item in publisher if isinstance(item, dict)), None)
        else:
            name = None
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None

    @staticmethod
    def _get_canonical_from_json_ld(node: dict, article_url: str) -> str | None:
        """Extract a canonical URL from a JSON-LD article node.

        Checks ``url`` and ``mainEntityOfPage`` (string, object with ``@id``
        or ``url``). Relative values are resolved against the article URL.
        """
        candidates: list[Any] = [node.get("url")]

        main_entity = node.get("mainEntityOfPage")
        if isinstance(main_entity, str):
            candidates.append(main_entity)
        elif isinstance(main_entity, dict):
            candidates.append(main_entity.get("@id"))
            candidates.append(main_entity.get("url"))
        elif isinstance(main_entity, list):
            for item in main_entity:
                if isinstance(item, str):
                    candidates.append(item)
                elif isinstance(item, dict):
                    candidates.append(item.get("@id"))
                    candidates.append(item.get("url"))

        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return urljoin(article_url, candidate.strip())
        return None

    def _get_meta_language(self, doc: HtmlElement) -> str | None:
        """Return the language string of the article, or None if it cannot be
        determined.
        """

        def get_if_valid(s: str | None) -> str | None:
            if s is None or len(s) < 2:
                return None

            # Handle ISO 639-3 codes (3 chars) that can be mapped to ISO 639-1
            s_lower = s[:3].lower()
            if s_lower in ISO639_3_TO_1:
                return ISO639_3_TO_1[s_lower]

            # Standard 2-char ISO 639-1 code
            s = s[:2]
            if re.search(RE_LANG, s):
                return s.lower()
            return None

        attr = get_if_valid(parsers.get_attribute(doc, "lang"))
        if attr:
            return attr

        for elem in META_LANGUAGE_TAGS:
            meta_tag = parsers.get_tags(doc, tag=elem["tag"], attribs={elem["attr"]: elem["value"]})

            if meta_tag:
                attr = get_if_valid(meta_tag[0])
                if attr:
                    return attr

        return None

    def _get_canonical_link(self, article_url: str, doc: HtmlElement) -> str | None:
        """Return the article's canonical URL

        Gets the first available value of:
        1. The rel=canonical tag
        2. The og:url tag
        """
        candidates = [node.get("href") for node in parsers.get_tags(doc, tag="link", attribs={"rel": "canonical"})]

        candidates.append(self._get_meta_field(doc, "og:url"))
        candidates = [c.strip() for c in candidates if c and c.strip()]

        if candidates:
            meta_url = candidates[0]
            parsed_meta_url = urlparse(meta_url)
            if not parsed_meta_url.hostname:
                # MIGHT not have a hostname in meta_url
                # parsed_url.path might be 'example.com/article.html' where
                # clearly example.com is the hostname
                parsed_article_url = urlparse(article_url)
                strip_hostname_in_meta_path = re.match(
                    rf".*{parsed_article_url.hostname}(?=/)/(.*)",
                    parsed_meta_url.path,
                )
                if strip_hostname_in_meta_path:
                    true_path = strip_hostname_in_meta_path.group(1)
                else:
                    true_path = parsed_meta_url.path

                # true_path may contain querystrings and fragments
                meta_url = urlunparse(
                    (
                        parsed_article_url.scheme,
                        parsed_article_url.hostname,
                        true_path,
                        "",
                        "",
                        "",
                    )
                )
            return meta_url

        return None

    def _get_metadata(self, doc: HtmlElement) -> dict[str, Any]:
        """Extracts metadata from the article's HTML"""
        data: dict[str, Any] = {}
        properties = parsers.get_tags(doc, "meta")
        for prop in properties:
            key = prop.attrib.get("property") or prop.attrib.get("name")
            value = prop.attrib.get("content") or prop.attrib.get("value")

            if not key or not value:
                continue

            key, value = key.strip(), value.strip()
            if value.isdigit():
                value = int(value)

            if ":" not in key:
                data[key] = value
                continue

            key = key.split(":")
            key_head = key.pop(0)
            ref = data.get(key_head, {})

            if isinstance(ref, str) or isinstance(ref, int):
                data[key_head] = {key_head: ref}
                ref = data[key_head]

            for idx, part in enumerate(key):
                if idx == len(key) - 1:
                    ref[part] = value
                    break
                if not ref.get(part):
                    ref[part] = {}
                elif isinstance(ref.get(part), str | int):
                    # Not clear what to do in this scenario,
                    # it's not always a URL, but an ID of some sort
                    ref[part] = {"identifier": ref[part]}
                ref = ref[part]
        return data

    def _get_tags(self, doc: HtmlElement) -> set[str]:
        """Extracts tags from the article's HTML"""
        elements = doc.xpath(A_HREF_TAG_SELECTOR)
        elements += doc.xpath(A_REL_TAG_SELECTOR)

        if not elements:
            return set()

        tags = [parsers.get_text(el) for el in elements if parsers.get_text(el)]
        return set(tags)

    def _get_meta_field(self, doc: HtmlElement, fields: str | list[str]) -> str:
        """Extract a given meta field from document."""
        if isinstance(fields, str):
            fields = [fields]
        for f in fields:
            meta_fields = parsers.get_metatags(doc, value=f)
            for meta_field in meta_fields:
                val = meta_field.get("content", "").strip()
                if val:
                    return val
        return ""
