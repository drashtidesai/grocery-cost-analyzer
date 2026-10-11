from html.parser import HTMLParser
from pathlib import Path


class Controls(HTMLParser):
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("input", "select", "option", "button") or "GLUX" in attrs.get("id", "") or attrs.get("id") == "nav-global-location-popover-link":
            print(tag, {k: v for k, v in attrs.items() if k in ("id", "name", "type", "placeholder", "role", "aria-label", "class", "value", "title")})


if __name__ == "__main__":
    Controls().feed(Path(".runtime/probe/amazon-location.html").read_text(encoding="utf-8"))
