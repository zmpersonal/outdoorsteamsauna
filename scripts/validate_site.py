from pathlib import Path
from html.parser import HTMLParser
import json
import re
import sys


ROOT = Path(__file__).resolve().parents[1]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.schemas = []
        self.in_schema = False
        self.schema_text = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {"a", "link", "script", "img"}:
            target = values.get("href") or values.get("src")
            if target:
                self.links.append(target)
        if tag == "script" and values.get("type") == "application/ld+json":
            self.in_schema = True
            self.schema_text = []

    def handle_data(self, data):
        if self.in_schema:
            self.schema_text.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.in_schema:
            self.schemas.append("".join(self.schema_text))
            self.in_schema = False


def local_target(url):
    path = url.split("#", 1)[0].split("?", 1)[0]
    if not path or not path.startswith("/"):
        return None
    candidate = ROOT / path.lstrip("/")
    if path.endswith("/"):
        candidate = candidate / "index.html"
    return candidate


errors = []
inhouse_links = []
html_files = list(ROOT.rglob("*.html"))
for file in html_files:
    parser = Links()
    try:
        parser.feed(file.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"HTML parse failure: {file.relative_to(ROOT)}: {exc}")
        continue
    for raw in parser.schemas:
        try:
            json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"Invalid JSON-LD: {file.relative_to(ROOT)}: {exc}")
    for link in parser.links:
        if "inhousewellness.com" in link:
            inhouse_links.append((file.relative_to(ROOT), link))
        target = local_target(link)
        if target and not target.exists():
            errors.append(f"Broken internal link: {file.relative_to(ROOT)} -> {link}")

data = json.loads((ROOT / "data/cities.json").read_text(encoding="utf-8"))
for city in data["cities"]:
    metrics = city["metrics"]
    if metrics["annual_snow_in"] is None and metrics["snow_data_status"] != "unavailable":
        errors.append(f"Snow status mismatch: {city['slug']}")
    if metrics["annual_snow_in"] == 0 and metrics["freeze_months"]:
        errors.append(f"Suspicious zero snowfall in freezing climate: {city['slug']}")

if len(inhouse_links) != 1 or str(inhouse_links[0][0]) != "recommended-retailer/index.html":
    errors.append(f"Expected exactly one InHouse Wellness link on the retailer page; found {inhouse_links}")
if not (ROOT / "llms.txt").exists() or "OAI-SearchBot" not in (ROOT / "robots.txt").read_text():
    errors.append("Machine-discovery files are incomplete")
if not re.search(r"<lastmod>\d{4}-\d{2}-\d{2}</lastmod>", (ROOT / "sitemap.xml").read_text()):
    errors.append("Sitemap lastmod dates are missing")

if errors:
    print("\n".join(errors))
    sys.exit(1)
print(f"Validated {len(html_files)} HTML files, {len(data['cities'])} city records and one disclosed InHouse Wellness link.")
