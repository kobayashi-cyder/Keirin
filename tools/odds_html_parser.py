#!/usr/bin/env python3
"""Convert an already loaded public odds page's HTML to rank-list text.

Standard-library-only, read-only parser. It does not make HTTP requests,
retrieve hidden APIs, save HTML, or bypass site restrictions.
"""
from html.parser import HTMLParser

BLOCKS = {
    "article", "aside", "blockquote", "br", "dd", "div", "dl",
    "dt", "footer", "form", "h1", "h2", "h3", "h4", "h5",
    "header", "hr", "li", "main", "ol", "p", "section",
    "table", "tbody", "td", "th", "thead", "tr", "ul",
}
SKIP = {"script", "style", "noscript", "svg", "head", "template"}


class _RankPageText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.blocked = 0
        self.in_body = False

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.in_body = True
        if tag in SKIP:
            self.blocked += 1
        if not self.in_body or self.blocked:
            return
        self.parts.append("\n" if tag in BLOCKS else " ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if not self.in_body:
            return
        if not self.blocked:
            self.parts.append("\n" if tag in BLOCKS else " ")
        if tag in SKIP and self.blocked:
            self.blocked -= 1
        if tag == "body":
            self.in_body = False

    def handle_data(self, data):
        if self.in_body and not self.blocked:
            self.parts.append(data)


def text_from_html(html):
    """Generate lines for strict downstream bet-combination validation.

    HTML may include alternative display layouts, so the downstream parser
    must reject extra/ambiguous ranked blocks instead of guessing markets.
    """
    if not isinstance(html, str):
        raise ValueError("HTML must be text")
    parser = _RankPageText()
    parser.feed(html)
    parser.close()
    return "".join(parser.parts)
