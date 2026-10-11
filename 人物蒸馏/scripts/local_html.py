"""Bounded UTF-8 HTML-to-text extraction; no rendering, URL fetching or scripts."""
from html.parser import HTMLParser

MAX_BYTES = 5 * 1024 * 1024
MAX_EVENTS = 100000
MAX_DEPTH = 256
BLOCKS = {"p", "div", "section", "article", "main", "header", "footer", "nav", "aside",
          "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "blockquote", "pre",
          "table", "tr", "td", "th", "br", "hr"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
OMIT = {"head", "script", "style", "template", "noscript", "iframe", "object", "svg", "math"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = []
        self.events = 0

    def tick(self):
        self.events += 1
        if self.events > MAX_EVENTS:
            raise ValueError("html_event_limit")

    def handle_starttag(self, tag, attrs):
        self.tick()
        attributes = dict(attrs)
        hidden = (any(v[1] for v in self.stack) or tag in OMIT or "hidden" in attributes
                  or (attributes.get("aria-hidden") or "").casefold() == "true")
        if not hidden and tag in BLOCKS:
            self.parts.append("\n")
        if tag not in VOID:
            self.stack.append((tag, hidden))
            if len(self.stack) > MAX_DEPTH:
                raise ValueError("html_depth_limit")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        self.tick()
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if not any(v[1] for v in self.stack) and tag in BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.tick()
        if not any(v[1] for v in self.stack):
            self.parts.append(data)

    def handle_comment(self, data):
        self.tick()


def extract_html(payload: bytes) -> str:
    if not isinstance(payload, bytes) or len(payload) > MAX_BYTES:
        raise ValueError("html_input_limit")
    source = payload.decode("utf-8-sig")
    if "\x00" in source:
        raise ValueError("html_nul")
    parser = _Text()
    parser.feed(source)
    parser.close()
    text = "\n".join(line for part in "".join(parser.parts).splitlines()
                     if (line := " ".join(part.split())))
    if not text:
        raise ValueError("html_empty_text")
    return text
