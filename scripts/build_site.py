from pathlib import Path
from datetime import date, datetime, timedelta
from statistics import median
import csv
import html
import json
import re


ROOT = Path(__file__).resolve().parents[1]
SITE = "https://outdoorsteamsauna.com"
BUILD_DATE = date(2026, 9, 16)
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def slug(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def esc(value, quote=True):
    return html.escape(str(value), quote=quote)


def clamp(value, low, high):
    return max(low, min(high, value))


def human_date(value):
    if not value:
        return "Not available"
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
        return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"
    except (ValueError, TypeError):
        return str(value)


def human_period(value):
    if not value:
        return "Not available"
    try:
        return datetime.strptime(value, "%Y-%m").strftime("%B %Y")
    except (ValueError, TypeError):
        return str(value)


def metric_values(monthly, rate):
    jan = monthly[0]["tmin_f"]
    precipitation = [m.get("prcp_in") for m in monthly if m.get("prcp_in") is not None]
    snowfall = [m.get("snow_in") for m in monthly if m.get("snow_in") is not None]
    annual_prcp = sum(precipitation) if len(precipitation) >= 10 else None
    annual_snow = sum(snowfall) if len(snowfall) >= 10 else None
    freeze_months = sum(1 for m in monthly if m["tmin_f"] < 32)
    cold = clamp((45 - jan) / 55, 0, 1)
    freeze = clamp(freeze_months / 7, 0, 1)
    wet = clamp((annual_prcp or 0) / 70, 0, 1)
    energy = clamp((rate - 10) / 25, 0, 1)
    climate_score = round(100 * (.60 * cold + .275 * freeze + .125 * wet), 1)
    cost_index = round(100 * energy, 1)
    planning_score = round(.80 * climate_score + .20 * cost_index, 1)
    warmup_hours = .95 + .45 * cold
    kwh = 9 * (warmup_hours + .60)
    cost = round(kwh * rate / 100, 2)
    label = ("Severe climate load" if climate_score >= 75 else "High climate load" if climate_score >= 55 else "Moderate climate load" if climate_score >= 35 else "Mild climate load")
    return {
        "score": planning_score,
        "climate_score": climate_score,
        "cost_index": cost_index,
        "planning_score": planning_score,
        "label": label,
        "jan_tmin_f": round(jan, 1),
        "annual_prcp_in": round(annual_prcp, 1) if annual_prcp is not None else None,
        "annual_snow_in": round(annual_snow, 1) if annual_snow is not None else None,
        "snow_data_status": "available" if annual_snow is not None else "unavailable",
        "freeze_months": freeze_months,
        "rate_cents": round(rate, 2),
        "session_cost_9kw": cost,
        "annual_cost_3x_week": int(round(cost * 3 * 52)),
    }


def normalize_existing_data(obj):
    """Migrate the checked-in v1 snapshot without inventing missing observations."""
    for city in obj["cities"]:
        old = city.get("metrics", {})
        monthly = city["monthly"]
        # v1 converted absent snowfall to zero. In a freezing climate an all-zero
        # record is treated as unknown until the updater selects a snow station.
        if old.get("annual_snow_in") == 0 and any(m["tmin_f"] < 32 for m in monthly):
            for month in monthly:
                month["snow_in"] = None
        city["metrics"] = metric_values(monthly, old.get("rate_cents", 0))
        recent = city.get("recent")
        if recent and recent.get("avg_f") is not None and recent.get("through") and recent.get("days"):
            try:
                end = datetime.strptime(recent["through"], "%Y-%m-%d").date()
                dates = [end - timedelta(days=offset) for offset in range(int(recent["days"]))]
                normal = sum(monthly[d.month - 1]["tavg_f"] for d in dates) / len(dates)
                recent.update({"normal_f": round(normal, 1), "delta_f": round(recent["avg_f"] - normal, 1), "from": min(dates).isoformat(), "comparison": "month-matched 1991–2020 normal"})
            except (ValueError, TypeError, ZeroDivisionError):
                city["recent"] = {}
        elif recent is None:
            city["recent"] = {}

    obj["cities"].sort(key=lambda c: c["metrics"]["climate_score"], reverse=True)
    for rank, city in enumerate(obj["cities"], 1):
        city["rank"] = rank
    for rank, city in enumerate(sorted(obj["cities"], key=lambda c: c["metrics"]["planning_score"], reverse=True), 1):
        city["planning_rank"] = rank
    for rank, city in enumerate(sorted(obj["cities"], key=lambda c: c["metrics"]["session_cost_9kw"], reverse=True), 1):
        city["cost_rank"] = rank

    meta = obj.setdefault("meta", {})
    meta.update({"dataset_version": "2.0", "methodology_version": "2.0"})
    meta["observations_through"] = max(((c.get("recent") or {}).get("through", "") for c in obj["cities"]), default="") or None
    periods = []
    for city in obj["cities"]:
        match = re.search(r"(\d{4}-\d{2})", city.get("electricity_source", ""))
        if match:
            periods.append(match.group(1))
    meta["electricity_period"] = max(periods, default=None)
    meta["notes"] = "NOAA/NCEI 1991–2020 climate normals and EIA state residential electricity rates. Recent observations use month-matched normals. Missing snowfall is null and excluded from snowfall rankings."
    return obj


def write(path, body):
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


def page_schema(page_type, title, description, canonical, extras=None):
    graph = [
        {"@type": "Organization", "@id": f"{SITE}/#organization", "name": "OutdoorSteamSauna.com", "url": f"{SITE}/", "description": "An independent climate, energy-cost and planning reference for outdoor traditional sauna projects."},
        {"@type": "WebSite", "@id": f"{SITE}/#website", "url": f"{SITE}/", "name": "OutdoorSteamSauna.com", "publisher": {"@id": f"{SITE}/#organization"}},
        {"@type": page_type, "@id": f"{SITE}{canonical}#page", "url": f"{SITE}{canonical}", "name": title, "description": description, "isPartOf": {"@id": f"{SITE}/#website"}, "publisher": {"@id": f"{SITE}/#organization"}},
    ]
    graph.extend(extras or [])
    return json.dumps({"@context": "https://schema.org", "@graph": graph}, separators=(",", ":"))


def head(title, description, canonical, page_type="WebPage", extras=None, robots="index,follow"):
    schema = page_schema(page_type, title, description, canonical, extras)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)}</title><meta name="description" content="{esc(description)}"><meta name="robots" content="{robots}"><link rel="canonical" href="{SITE}{canonical}"><link rel="icon" href="/favicon.svg" type="image/svg+xml"><meta name="theme-color" content="#153e32"><meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(description)}"><meta property="og:type" content="website"><meta property="og:url" content="{SITE}{canonical}"><meta name="twitter:card" content="summary"><link rel="stylesheet" href="/assets/style.css"><script type="application/ld+json">{schema}</script></head><body>'''


def nav():
    return '''<header class="top"><div class="wrap nav"><a class="brand" href="/"><span>OSS</span>OutdoorSteamSauna.com</a><nav class="navlinks" aria-label="Primary"><a href="/climate-index/">Climate Index</a><a href="/heater-sizing/">Heater Sizing</a><a href="/operating-cost/">Cost</a><a href="/rankings/">Rankings</a><a href="/research/">Research</a><a href="/data-download/">Data</a></nav></div></header>'''


def footer():
    return '''<footer class="footer"><div class="wrap footer-grid"><div><b>OutdoorSteamSauna.com</b><p>An independent climate, electricity-cost and planning reference for outdoor traditional sauna projects. Estimates are for comparison—not electrical, structural or code approval.</p></div><div><b>Research & tools</b><p><a href="/research/">Research reports</a><br><a href="/climate-index/">Climate index</a><br><a href="/heater-sizing/">Heater sizing</a><br><a href="/operating-cost/">Operating cost</a><br><a href="/data-download/">Download data</a></p></div><div><b>Standards</b><p><a href="/about/">About</a><br><a href="/methodology/">Methodology</a><br><a href="/editorial-standards/">Editorial standards</a><br><a href="/sources/">Source directory</a><br><a href="/changelog/">Changelog</a><br><a href="/corrections/">Corrections</a><br><a href="/recommended-retailer/">Recommended retailer</a></p></div></div></footer><script src="/assets/app.js"></script></body></html>'''


def freshness(meta):
    return f'''<aside class="freshness" aria-label="Dataset freshness"><div class="wrap freshness-grid"><span><b>Dataset v{esc(meta.get('dataset_version', '2.0'))}</b> · updated {esc(human_date(meta.get('generated')))}</span><span>NOAA observations through {esc(human_date(meta.get('observations_through')))}</span><span>EIA electricity rates: {esc(human_period(meta.get('electricity_period')))}</span><a href="/changelog/">View changelog</a></div></aside>'''


def snow_text(value):
    return "Unavailable" if value is None else f"{value:.1f} in"


data_path = ROOT / "data/cities.json"
data = normalize_existing_data(json.loads(data_path.read_text(encoding="utf-8")))
data_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
cities, meta = data["cities"], data["meta"]
heaters = json.loads((ROOT / "data/heaters.json").read_text(encoding="utf-8"))
states = {}
for city in cities:
    states.setdefault(city["state"], []).append(city)
climate_median = round(median(c["metrics"]["climate_score"] for c in cities), 1)
cost_median = round(median(c["metrics"]["session_cost_9kw"] for c in cities), 2)


def city_analysis(city):
    m = city["metrics"]
    relation = "above" if m["climate_score"] > climate_median else "below"
    cost_relation = "above" if m["session_cost_9kw"] > cost_median else "below"
    primary = "winter temperature and freeze exposure" if m["freeze_months"] >= 4 else "electricity price" if m["cost_index"] >= 55 else "rain and exterior moisture management" if (m["annual_prcp_in"] or 0) >= 45 else "site design and equipment selection rather than severe climate"
    snow_sentence = f"The selected snowfall record reports {m['annual_snow_in']:.1f} inches annually." if m["annual_snow_in"] is not None else "The selected station does not provide a complete snowfall normal, so snowfall is not reported or ranked for this city."
    suitable = [MONTHS[x["month"] - 1] for x in city["monthly"] if 45 <= x["tavg_f"] <= 72]
    months = ", ".join(suitable[:4]) if suitable else "the locally mildest part of the year"
    return f'''<p><b>{esc(city['city'])} has a {m['label'].lower()}.</b> Its Climate Stress Score of {m['climate_score']} is {relation} the indexed-metro median of {climate_median}. The January normal low is {m['jan_tmin_f']}°F, and {m['freeze_months']} month{'s have' if m['freeze_months'] != 1 else ' has'} a normal low below freezing.</p><p>The primary planning issue in this profile is {primary}. The standardized 9 kW session estimate is ${m['session_cost_9kw']:.2f}, {cost_relation} the indexed-metro median of ${cost_median:.2f}; this uses the {esc(city['state_name'])} statewide residential electricity average, not a city utility quote. {snow_sentence}</p><p>For construction or maintenance work that is easier in moderate conditions, the monthly normals point to {months}. This is a climate-screening observation, not a construction schedule or permit recommendation.</p>'''


def nearest_peer(city):
    others = [c for c in cities if c["slug"] != city["slug"]]
    return min(others, key=lambda c: abs(c["metrics"]["climate_score"] - city["metrics"]["climate_score"]) + abs(c["metrics"]["rate_cents"] - city["metrics"]["rate_cents"]) / 10)


# Homepage
rows = ''.join(f'''<tr data-city-row="{esc((c['city'] + ' ' + c['state']).lower())}"><td class="rank">#{c['rank']}</td><td><a href="/cities/{c['slug']}/"><b>{esc(c['city'])}, {c['state']}</b></a></td><td class="score">{c['metrics']['climate_score']}</td><td>{c['metrics']['jan_tmin_f']}°F</td><td>{c['metrics']['rate_cents']}¢</td><td>${c['metrics']['session_cost_9kw']:.2f}</td></tr>''' for c in cities[:30])
home = head("Outdoor Sauna Climate Index, Cost Data & Planning Tools", "Independent U.S. outdoor sauna climate scores, electricity-cost estimates, heater sizing tools and downloadable NOAA/EIA-derived data.", "/") + nav() + freshness(meta) + f'''<main><section class="hero"><div class="wrap"><div class="eyebrow">Independent U.S. outdoor sauna data</div><h1>Plan the heat for the climate outside.</h1><p>Compare physical climate stress separately from electricity cost across {len(cities)} U.S. metros. Every profile identifies its NOAA station, state-level EIA rate, calculation method and update period.</p><div class="hero-actions"><a class="btn primary" href="/climate-index/">Explore the Climate Stress Index</a><a class="btn" href="/research/2026-outdoor-sauna-climate-report/">Read the 2026 report</a></div></div></section><section class="section compact"><div class="wrap"><div class="grid"><div class="card stat"><small>METROS INDEXED</small><b>{len(cities)}</b><span class="muted">across {len(states)} states/DC</span></div><div class="card stat"><small>CLIMATE BASELINE</small><b>{esc(meta.get('normal_period', '1991–2020'))}</b><span class="muted">NOAA/NCEI normals</span></div><div class="card stat"><small>NOAA PROFILES</small><b>{meta.get('live_noaa_cities', 0)}</b><span class="muted">station-linked metros</span></div><div class="card stat"><small>METHODOLOGY</small><b>v{esc(meta.get('methodology_version', '2.0'))}</b><span class="muted">climate and cost separated</span></div></div></div></section><section class="section"><div class="wrap grid"><div class="index-panel"><div class="eyebrow">Climate Stress Index</div><h2>Where the outdoor environment creates the greatest planning load.</h2><p>The Climate Stress Score uses temperature, freeze exposure and precipitation only. Electricity cost is shown separately.</p><div class="searchbox"><input id="citySearch" aria-label="Search city or state" placeholder="Search city or state"></div><div class="table-wrap"><table><thead><tr><th>Climate rank</th><th>Metro</th><th>Climate score</th><th>Jan. low</th><th>State power</th><th>9 kW/session</th></tr></thead><tbody>{rows}</tbody></table></div><p><a class="btn dark" href="/climate-index/">View all {len(cities)} metros</a></p></div><aside class="card side-panel calc"><div class="eyebrow">Quick estimator</div><h3>What does a session cost?</h3><form id="costForm"><div class="field"><label>Heater kW<input name="kw" type="number" value="9" step="0.1"></label></div><div class="field"><label>Electricity ¢/kWh<input name="rate" type="number" value="15" step="0.1"></label></div><div class="field"><label>Warm-up minutes<input name="warm" type="number" value="70"></label></div><div class="field"><label>Session minutes<input name="session" type="number" value="60"></label></div><div class="field"><label>Sessions/week<input name="freq" type="number" value="3"></label></div></form><div id="costResult" class="result" aria-live="polite"></div><p><a class="btn" href="/operating-cost/">Full cost calculator</a></p></aside></div></section><section class="section tint"><div class="wrap three-up"><article><div class="eyebrow">Climate Stress Score</div><h3>Physical environment only</h3><p>January normal low, freeze months and annual precipitation. It answers: how demanding is the outdoor climate?</p></article><article><div class="eyebrow">Operating Cost Index</div><h3>Energy economics only</h3><p>State residential electricity price and a transparent 9 kW session scenario. It answers: how expensive is standardized electric operation?</p></article><article><div class="eyebrow">Planning Index</div><h3>Optional combined view</h3><p>An 80% climate and 20% energy composite for readers who want one comparison number. The components remain visible.</p></article></div></section><section class="section"><div class="wrap editorial"><div class="eyebrow">Why this source exists</div><h2>An outdoor sauna in Miami and one in Minneapolis are not the same planning problem.</h2><p>This project converts official public climate and energy data into reproducible planning comparisons. It publishes the method, machine-readable records, limitations, corrections and update history so readers, publishers and AI systems can inspect the evidence behind each claim.</p><div class="callout"><b>The indexes are comparison tools, not building-code or heater-approval tools.</b> Final heater selection, clearances, wiring and installation must follow current manufacturer instructions and local requirements.</div><p><a href="/methodology/">Read methodology v2.0 →</a></p></div></section><section class="section"><div class="wrap grid"><div class="card span-7"><div class="eyebrow">Traditional sauna, real steam</div><h2>Water on hot sauna stones creates löyly. It does not turn the enclosure into a steam room.</h2><p>A traditional sauna uses a heater and stones; permitted water on hot stones creates a temporary humidity burst. A dedicated steam room is a sustained, near-saturated-humidity system with different construction requirements.</p><a class="btn dark" href="/guides/steam-vs-sauna/">Read the terminology guide</a></div><div class="card span-5"><div class="eyebrow">Open research</div><h3>Download, cite or embed the data.</h3><p>Use the CSV/JSON datasets, cite methodology v2.0, or place a city Climate Stress badge on another site with source attribution.</p><a class="btn" href="/data-download/">Open data and citation guide →</a></div></div></section></main>''' + footer()
write("index.html", home)


# Climate index
all_rows = ''.join(f'''<tr data-city-row="{esc((c['city'] + ' ' + c['state']).lower())}"><td class="rank">#{c['rank']}</td><td><a href="/cities/{c['slug']}/"><b>{esc(c['city'])}, {c['state']}</b></a></td><td>{c['metrics']['climate_score']}</td><td>{c['metrics']['label']}</td><td>{c['metrics']['jan_tmin_f']}°F</td><td>{c['metrics']['freeze_months']}</td><td>{snow_text(c['metrics']['annual_snow_in'])}</td><td>{c['metrics']['cost_index']}</td><td>${c['metrics']['session_cost_9kw']:.2f}</td></tr>''' for c in cities)
write("climate-index/index.html", head("U.S. Outdoor Sauna Climate Stress Index", "Compare physical outdoor-sauna climate stress, electricity-cost indexes and source data across 75 U.S. metros.", "/climate-index/") + nav() + freshness(meta) + f'''<main><section class="city-hero"><div class="wrap"><div class="eyebrow">Climate Stress Index · methodology v2.0</div><h1>{len(cities)} metros, climate and cost kept separate.</h1><p>Climate scores use temperature, freeze exposure and precipitation. The cost index uses state residential electricity prices. Missing snowfall displays as unavailable and is never treated as zero.</p></div></section><section class="section"><div class="wrap"><div class="searchbox"><input id="citySearch" aria-label="Filter by city or state" placeholder="Filter by city or state"></div><div class="table-wrap"><table><thead><tr><th>Climate rank</th><th>Metro</th><th>Climate</th><th>Class</th><th>Jan. low</th><th>Freeze months</th><th>Snow</th><th>Cost index</th><th>9 kW/session</th></tr></thead><tbody>{all_rows}</tbody></table></div><p class="data-note">Snow rankings and totals include only stations with at least 10 months of explicit snowfall normals. Electricity values are statewide residential averages.</p></div></section></main>''' + footer())


# Rankings
valid_snow = [c for c in cities if c["metrics"]["annual_snow_in"] is not None]
recent_cities = [c for c in cities if c.get("recent") and c["recent"].get("comparison")]
ranking_sets = [
    ("Highest climate stress", cities[:15], "climate_score", "Climate score", "Temperature, freeze exposure and precipitation only."),
    ("Lowest climate stress", list(reversed(cities[-15:])), "climate_score", "Climate score", "The mildest physical climate profiles in the indexed set."),
    ("Highest estimated 9 kW session cost", sorted(cities, key=lambda c: c["metrics"]["session_cost_9kw"], reverse=True)[:15], "session_cost_9kw", "Per session", "State electricity price combined with the standardized climate-adjusted session model."),
    ("Most freeze-exposed metros", sorted(cities, key=lambda c: (c["metrics"]["freeze_months"], -c["metrics"]["jan_tmin_f"]), reverse=True)[:15], "freeze_months", "Freeze months", "Months whose 1991–2020 normal minimum is below 32°F."),
    ("Wettest indexed metros", sorted(cities, key=lambda c: c["metrics"]["annual_prcp_in"] or -1, reverse=True)[:15], "annual_prcp_in", "Annual precipitation", "NOAA/NCEI annual precipitation normal from the selected station."),
    ("Snowiest metros with complete records", sorted(valid_snow, key=lambda c: c["metrics"]["annual_snow_in"], reverse=True)[:15], "annual_snow_in", "Annual snowfall", "Cities with incomplete snowfall records are excluded, not assigned zero."),
]
if recent_cities:
    ranking_sets.append(("Largest recent temperature departures", sorted(recent_cities, key=lambda c: c["recent"]["delta_f"], reverse=True)[:15], "recent", "Departure", "Recent observations compared with normals matched to each observation month."))
rank_blocks = ""
for title, items, key, value_label, explanation in ranking_sets:
    trs = []
    for index, city in enumerate(items, 1):
        if key == "recent":
            value = f"{city['recent']['delta_f']:+.1f}°F"
        elif key == "session_cost_9kw":
            value = f"${city['metrics'][key]:.2f}"
        elif key in ("annual_prcp_in", "annual_snow_in"):
            value = f"{city['metrics'][key]:.1f} in"
        else:
            value = city["metrics"][key]
        trs.append(f'''<tr><td>{index}</td><td><a href="/cities/{city['slug']}/">{esc(city['city'])}, {city['state']}</a></td><td>{value}</td></tr>''')
    rank_blocks += f'''<article class="card ranking-card"><h2>{title}</h2><p>{explanation}</p><div class="table-wrap"><table><thead><tr><th>#</th><th>Metro</th><th>{value_label}</th></tr></thead><tbody>{''.join(trs)}</tbody></table></div></article>'''
write("rankings/index.html", head("Outdoor Sauna Climate, Cost, Freeze & Snow Rankings", "Answer-shaped rankings for outdoor sauna climate stress, operating cost, freeze exposure, precipitation and complete snowfall records.", "/rankings/") + nav() + freshness(meta) + f'''<main><section class="city-hero"><div class="wrap"><div class="eyebrow">Reproducible rankings</div><h1>Outdoor sauna conditions, ranked by the underlying factor.</h1><p>Each table answers one question. Physical climate and energy price are not blended unless a reader deliberately chooses the Planning Index.</p></div></section><section class="section"><div class="wrap ranking-grid">{rank_blocks}</div><div class="wrap action-row"><a class="btn dark" href="/data-download/">Download the source data</a><a class="btn" href="/methodology/">Audit the formulas</a></div></section></main>''' + footer())


# Calculators
write("operating-cost/index.html", head("Electric Outdoor Sauna Cost Calculator", "Estimate electricity use and cost for an outdoor electric sauna by heater output, rate, warm-up time and weekly frequency.", "/operating-cost/", "WebApplication", [{"@type": "SoftwareApplication", "name": "Outdoor Sauna Electricity Cost Calculator", "applicationCategory": "UtilitiesApplication", "operatingSystem": "Any", "url": f"{SITE}/operating-cost/", "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}}]) + nav() + freshness(meta) + '''<main><section class="city-hero"><div class="wrap"><div class="eyebrow">Transparent scenario calculator</div><h1>Turn heater kW into dollars per session.</h1><p>Change every assumption yourself. No model-specific performance claim is hidden inside the result.</p></div></section><section class="section"><div class="wrap grid"><div class="card calc span-6"><form id="costForm"><div class="field"><label>Heater output (kW)<input name="kw" type="number" value="9" min="1" max="40" step="0.1"></label></div><div class="field"><label>Electricity rate (¢/kWh)<input name="rate" type="number" value="15" min="0" max="100" step="0.1"></label></div><div class="field"><label>Warm-up minutes<input name="warm" type="number" value="70" min="0" max="300"></label></div><div class="field"><label>Session minutes<input name="session" type="number" value="60" min="0" max="300"></label></div><div class="field"><label>Sessions per week<input name="freq" type="number" value="3" min="0" max="21"></label></div></form><div id="costResult" class="result" aria-live="polite"></div></div><div class="card span-6"><h2>What the calculator assumes</h2><p>Warm-up is modeled at full heater output. During the session, the estimator uses a 60% full-load-equivalent duty assumption rather than continuous maximum output.</p><p>Actual cycling, outdoor temperature, insulation, glass area, ventilation, door openings and user behavior can materially change consumption.</p><h3>Formula</h3><p><code>kWh = kW × (warm-up hours + session hours × 0.60)</code></p><p><code>cost = kWh × electricity rate</code></p></div></div></section></main>''' + footer())

heater_json = json.dumps(heaters, separators=(",", ":"))
heater_table = ''.join(f'''<tr><td>{esc(item['brand'])}</td><td>{esc(item['model'])}</td><td>{item['kw']} kW</td><td>{item['min_ft3']}–{item['max_ft3']} ft³</td><td><a href="{esc(item['source'])}" rel="nofollow noopener">manufacturer source</a></td></tr>''' for item in heaters)
write("heater-sizing/index.html", head("Outdoor Sauna Heater Sizing Calculator", "Calculate raw and adjusted sauna volume, account for glass and compare the result with source-linked manufacturer ranges.", "/heater-sizing/", "WebApplication", [{"@type": "SoftwareApplication", "name": "Outdoor Sauna Heater Sizing Calculator", "applicationCategory": "UtilitiesApplication", "operatingSystem": "Any", "url": f"{SITE}/heater-sizing/", "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}}]) + nav() + '''<main><section class="city-hero"><div class="wrap"><div class="eyebrow">Heater sizing</div><h1>Size the room before you size the heater.</h1><p>Calculate raw room volume, account for glass or other uninsulated surfaces, and verify the result against the exact manufacturer instructions.</p></div></section><section class="section"><div class="wrap grid"><div class="card calc span-6"><form id="heaterForm"><div class="field"><label>Interior length (ft)<input name="length" type="number" value="6" min="1" max="30" step="0.1"></label></div><div class="field"><label>Interior width (ft)<input name="width" type="number" value="6" min="1" max="30" step="0.1"></label></div><div class="field"><label>Interior height (ft)<input name="height" type="number" value="7" min="1" max="15" step="0.1"></label></div><div class="field"><label>Glass / uninsulated surface (ft²)<input name="glass" type="number" value="15" min="0" max="500"></label></div></form><div id="heaterResult" class="result" aria-live="polite"></div></div><div class="card span-6"><h2>Surface adjustment</h2><p>This planning calculator uses <b>3.3 ft³ of added effective volume per ft² of uninsulated surface</b>, the imperial form of a 1 m³-per-m² approach published in sauna-heater guidance. It is not a universal code rule.</p><p>Manufacturers can use different logic. Always use the exact heater manual or calculator for final selection.</p><p><a href="https://huumsauna.com/sauna-heater-size-calculator-how-to-choose-the-right-power-rating-for-your-sauna/" rel="nofollow noopener">HUUM sizing methodology →</a></p></div></div><div class="wrap table-section"><h2>Source-linked reference ranges</h2><div class="table-wrap"><table><thead><tr><th>Brand</th><th>Model</th><th>Output</th><th>Rated room volume</th><th>Source</th></tr></thead><tbody>''' + heater_table + '''</tbody></table></div><p class="data-note">Reference sample, not a product recommendation or approval for every outdoor installation.</p></div></section><script>window.HEATERS=''' + heater_json + '''</script></main>''' + footer())


# Editorial guides
guide_pages = {
    "guides/steam-vs-sauna/index.html": (
        "Outdoor Steam Sauna vs. Traditional Sauna",
        "Why water on traditional sauna stones creates löyly while a steam room remains a different high-humidity system.",
        "/guides/steam-vs-sauna/",
        '''<section class="city-hero"><div class="wrap"><div class="eyebrow">Terminology</div><h1>Can an outdoor traditional sauna make steam?</h1><p>Yes—if the heater and stones are designed for water. But that does not make the enclosure a steam room.</p></div></section><section class="section"><article class="wrap editorial"><h2>Traditional sauna: heat first, humidity in bursts.</h2><p>A traditional sauna heats the room and a mass of sauna stones. When suitable water is thrown onto hot stones, it vaporizes and produces a temporary humidity experience commonly described with the Finnish word <i>löyly</i>.</p><p>Heater design and stone mass affect that experience. Water should only be used when the manufacturer permits it, and the current operating instructions control.</p><h2>A steam room is a different system.</h2><p>A dedicated steam room uses a steam generator and is designed around sustained, very high humidity. That changes waterproofing, surfaces, drainage and construction. A traditional outdoor sauna kit should not be treated as a steam-room enclosure unless its manufacturer explicitly approves that use.</p><div class="callout">Domain terminology reflects common search language. Throughout this site, “outdoor sauna” means a traditional heated sauna unless a dedicated steam room is explicitly identified.</div><p class="source-list"><a href="https://www.harvia.com/en-US/products/HPC900400/cilindro-pc90-90-kw-steel" rel="nofollow noopener">Harvia heater reference →</a></p></article></section>'''
    ),
    "guides/outdoor-installation/index.html": (
        "Outdoor Sauna Installation Planning Guide",
        "A planning checklist for outdoor traditional sauna siting, foundation, drainage, electrical access and weather exposure.",
        "/guides/outdoor-installation/",
        '''<section class="city-hero"><div class="wrap"><div class="eyebrow">Site planning</div><h1>Plan the site before the sauna arrives.</h1></div></section><section class="section"><article class="wrap editorial"><h2>Five constraints matter early.</h2><h3>1. Foundation and level</h3><p>Follow the sauna manufacturer’s base requirements. The assembly needs stable, level support appropriate for its weight and local soil or frost conditions.</p><h3>2. Water management</h3><p>Keep roof runoff, irrigation and standing water away from the structure. Freeze-thaw climates make drainage and site grading particularly important.</p><h3>3. Electrical route</h3><p>Plan conductor route, disconnects and service capacity with a qualified electrician before final placement of an electric-heated sauna.</p><h3>4. Delivery and service access</h3><p>Account for crate or panel delivery, assembly clearances and future heater service access.</p><h3>5. Weather exposure</h3><p>Wind-driven rain, snow load, UV exposure and freeze cycles vary by property. Use the manufacturer’s exterior-treatment and maintenance instructions.</p><p><a class="btn dark" href="/climate-index/">Check your metro climate profile</a></p></article></section>'''
    ),
    "guides/insulation/index.html": (
        "Outdoor Sauna Insulation and Glass Planning",
        "How insulation, glass and other uninsulated surfaces affect effective sauna volume and heater sizing.",
        "/guides/insulation/",
        '''<section class="city-hero"><div class="wrap"><div class="eyebrow">Thermal envelope</div><h1>Glass looks great. The heater still has to cover the loss.</h1></div></section><section class="section"><article class="wrap editorial"><p>Heater manufacturers account for glass and other uninsulated surfaces because they increase heat load relative to insulated timber walls. Begin with physical interior volume, apply the relevant manufacturer’s surface adjustment, then check the resulting effective volume against the exact heater range.</p><p>Cold conditions do not justify ignoring a manufacturer’s minimum room volume or installing the largest heater that fits. Control placement, clearances and approved room-volume range remain model-specific.</p><a class="btn dark" href="/heater-sizing/">Open the heater sizing tool</a></article></section>'''
    ),
    "guides/electrical/index.html": (
        "Outdoor Sauna Electrical Planning",
        "Why heater output, electricity consumption and electrical installation requirements are separate questions.",
        "/guides/electrical/",
        '''<section class="city-hero"><div class="wrap"><div class="eyebrow">Electrical planning</div><h1>Heater kW is an energy number—not a wiring plan.</h1></div></section><section class="section"><article class="wrap editorial"><p>The cost calculator can estimate kWh, but breaker size, conductor size, controls, disconnects, protection requirements and permitted installation depend on the exact heater, control package, jurisdiction and manufacturer instructions.</p><div class="callout">Use a qualified electrician and the exact current heater manual for final electrical design.</div><p>For cost comparison, use the rate on your utility bill when available. City pages use an EIA state residential average so every metro can be compared with the same type of input.</p><p><a class="btn dark" href="/operating-cost/">Estimate energy cost</a></p></article></section>'''
    ),
}
for path, (title, description, canonical, body) in guide_pages.items():
    article_schema = {"@type": "TechArticle", "headline": title, "dateModified": BUILD_DATE.isoformat(), "mainEntityOfPage": f"{SITE}{canonical}", "author": {"@id": f"{SITE}/#organization"}}
    write(path, head(title, description, canonical, "TechArticle", [article_schema]) + nav() + f'<main>{body}</main>' + footer())


# Methodology and trust pages
write("methodology/index.html", head("Methodology v2.0 — Outdoor Sauna Climate Index", "Sources, formulas, missing-data rules, station selection, score separation and limitations for the Outdoor Sauna Climate Index.", "/methodology/", "TechArticle", [{"@type": "TechArticle", "headline": "Outdoor Sauna Climate Index Methodology v2.0", "dateModified": BUILD_DATE.isoformat(), "author": {"@id": f"{SITE}/#organization"}}]) + nav() + freshness(meta) + f'''<main><section class="city-hero"><div class="wrap"><div class="eyebrow">Methodology v2.0</div><h1>How the indexes are built.</h1><p>Version 2.0 separates physical climate from electricity economics and makes missing-data rules explicit.</p></div></section><section class="section"><article class="wrap editorial wide"><h2>1. Geographic unit</h2><p>The dataset covers {len(cities)} selected U.S. metros. Each city has a latitude/longitude reference point used only to locate nearby official climate stations. It does not represent every property within the metro.</p><h2>2. Climate baseline</h2><p>The updater targets NOAA/NCEI 1991–2020 Monthly Climate Normals. It selects a nearby station with complete temperature normals and useful precipitation coverage. The station ID, name and distance are published on each city page.</p><h2>3. Snowfall is nullable</h2><p>Snowfall is a separate field with three possible meanings: a measured zero, a positive normal, or unavailable. If the selected temperature station lacks adequate snowfall coverage, the updater searches nearby official stations for a usable snow series. If none is found, snowfall remains <code>null</code> and the city is excluded from snow rankings.</p><h2>4. Recent temperature context</h2><p>Recent station observations are optional. Each observed day is compared with the 1991–2020 monthly normal for that same calendar month. The displayed departure is therefore period-matched across a window that crosses month boundaries. It is still an approximation based on monthly, not daily, normals.</p><h2>5. Electricity</h2><p>The updater retrieves the latest available monthly residential electricity price by state from the U.S. Energy Information Administration. A city page’s rate is a <b>statewide residential average</b>, not the local utility tariff.</p><h2>6. Climate Stress Score</h2><div class="formula"><code>100 × (0.60 × cold + 0.275 × freeze exposure + 0.125 × precipitation exposure)</code></div><p><b>Cold:</b> January normal minimum scaled from 45°F (0) to −10°F (1). <b>Freeze exposure:</b> months with a normal minimum below 32°F divided by seven, capped at one. <b>Precipitation exposure:</b> annual precipitation divided by 70 inches, capped at one.</p><h2>7. Operating Cost Index</h2><div class="formula"><code>100 × clamp((rate − 10¢) ÷ 25¢, 0, 1)</code></div><p>The dollar scenario assumes a 9 kW heater, climate-adjusted warm-up full-load-equivalent hours and 60% full-load-equivalent operation during a one-hour session.</p><h2>8. Optional Planning Index</h2><p>For readers who want a single composite, the Planning Index is 80% Climate Stress Score and 20% Operating Cost Index. It is not labeled as physical climate stress.</p><h2>9. Update cadence</h2><p>The workflow runs weekly, regenerates dependent pages and validates the output. A prior climate record is preserved if no complete nearby station can be retrieved during a run.</p><h2>10. Limitations</h2><p>The indexes do not model a particular enclosure, wind, solar gain, thermal bridging, heater efficiency, utility tariff, electrical service or local code. They are standardized comparisons—not guarantees, permit advice or final sizing instructions.</p><p class="source-list"><a href="https://www.ncei.noaa.gov/products/land-based-station/us-climate-normals" rel="nofollow noopener">NOAA U.S. Climate Normals →</a><br><a href="https://www.ncei.noaa.gov/support/access-data-service-api-user-documentation" rel="nofollow noopener">NCEI Access Data Service →</a><br><a href="https://www.eia.gov/opendata/browser/electricity/retail-sales" rel="nofollow noopener">EIA retail electricity data →</a></p></article></section></main>''' + footer())

write("about/index.html", head("About OutdoorSteamSauna.com", "Who operates this independent outdoor-sauna climate and planning reference, what it publishes and how commerce is separated from research.", "/about/") + nav() + '''<main><section class="city-hero"><div class="wrap"><div class="eyebrow">About the project</div><h1>A narrow, inspectable reference for outdoor sauna planning.</h1></div></section><section class="section"><article class="wrap editorial"><p>OutdoorSteamSauna.com publishes climate comparisons, energy-cost scenarios, heater-sizing tools and practical planning guides for outdoor traditional sauna projects in the United States.</p><p>The project is built around primary public data from NOAA/NCEI and the U.S. Energy Information Administration. Each city record exposes the selected station, geographic distance, source period, update date and formula used to derive the score.</p><h2>What we do</h2><ul><li>Maintain a reproducible city-level climate and electricity dataset.</li><li>Publish machine-readable CSV and JSON files.</li><li>Explain where calculations are estimates and where data are unavailable.</li><li>Correct material errors publicly.</li><li>Keep commercial recommendations outside research formulas.</li></ul><h2>What we do not do</h2><p>We do not approve products, design electrical systems, issue permits, predict site-specific performance or substitute for a manufacturer, electrician, engineer or local authority.</p><h2>Commercial separation</h2><p>The site contains a clearly labeled recommended-retailer page. Retail relationships and compensation are not inputs to city scores, research findings, rankings or calculators.</p></article></section></main>''' + footer())

write("editorial-standards/index.html", head("Editorial and Data Standards", "Source hierarchy, update rules, corrections policy and commercial-independence standards for OutdoorSteamSauna.com.", "/editorial-standards/") + nav() + '''<main><section class="city-hero"><div class="wrap"><div class="eyebrow">Editorial and data standards</div><h1>Claims should be traceable to a source or disclosed calculation.</h1></div></section><section class="section"><article class="wrap editorial"><h2>Source hierarchy</h2><ol><li>Government datasets and technical documentation.</li><li>Current manufacturer manuals and official specifications.</li><li>Derived calculations whose inputs and formulas are published.</li><li>Secondary sources only when a primary source is unavailable.</li></ol><h2>Data publication rules</h2><ul><li>Missing values remain missing; they are not converted to zero.</li><li>State averages are labeled as state averages.</li><li>Modeled values are labeled as estimates.</li><li>Rankings exclude records that lack the required field.</li><li>Material method changes receive a version number and changelog entry.</li></ul><h2>Corrections</h2><p>Material errors are fixed in the dataset and documented with the date, affected field and nature of the change.</p><h2>Commercial independence</h2><p>No retailer payment, affiliate relationship, price or preference changes a climate score, cost index, research table or city rank. Commerce is disclosed and isolated on a dedicated page.</p><h2>Safety boundary</h2><p>Planning information is not electrical, engineering, medical, code or permitting advice.</p></article></section></main>''' + footer())

write("sources/index.html", head("Outdoor Sauna Data Source Directory", "Primary climate, electricity and manufacturer sources used by OutdoorSteamSauna.com.", "/sources/") + nav() + f'''<main><section class="city-hero"><div class="wrap"><div class="eyebrow">Source directory</div><h1>Primary sources behind the dataset and tools.</h1></div></section><section class="section"><div class="wrap source-cards"><article class="card"><h2>NOAA/NCEI Climate Normals</h2><p>1991–2020 monthly temperature, precipitation and, when available, snowfall normals.</p><a href="https://www.ncei.noaa.gov/products/land-based-station/us-climate-normals" rel="nofollow noopener">Official NOAA overview →</a></article><article class="card"><h2>NOAA/NCEI recent observations</h2><p>Optional daily summaries used for recent temperature context. Coverage runs through {esc(human_date(meta.get('observations_through')))}.</p><a href="https://www.ncei.noaa.gov/support/access-data-service-api-user-documentation" rel="nofollow noopener">Official API documentation →</a></article><article class="card"><h2>U.S. Energy Information Administration</h2><p>Monthly state residential retail electricity prices. Current period: {esc(human_period(meta.get('electricity_period')))}.</p><a href="https://www.eia.gov/opendata/browser/electricity/retail-sales" rel="nofollow noopener">Official EIA dataset →</a></article><article class="card"><h2>Manufacturer documentation</h2><p>Current manufacturer specification pages are linked directly in heater-sizing and terminology guides.</p><a href="/heater-sizing/">View source-linked ranges →</a></article></div></section></main>''' + footer())

write("changelog/index.html", head("Dataset and Methodology Changelog", "Version history for OutdoorSteamSauna.com data, formulas, rankings and publication changes.", "/changelog/") + nav() + f'''<main><section class="city-hero"><div class="wrap"><div class="eyebrow">Changelog</div><h1>What changed, and when.</h1></div></section><section class="section"><div class="wrap timeline"><article><time datetime="2026-09-16">September 16, 2026 · v2.0</time><h2>Climate and energy separated</h2><ul><li>Created separate Climate Stress, Operating Cost and optional Planning indexes.</li><li>Changed missing snowfall from zero to null and excluded incomplete records from snow rankings.</li><li>Changed recent-temperature comparison to month-matched normals.</li><li>Added explicit source periods, station distance, methodology version and dataset dates.</li><li>Added research, source, standards, corrections and citation resources.</li></ul></article><article><time datetime="{esc(meta.get('generated'))}">{esc(human_date(meta.get('generated')))} · v1 data refresh</time><h2>NOAA and EIA refresh</h2><p>{meta.get('live_noaa_cities', 0)} metro climate profiles and {meta.get('live_eia_states', 0)} state/DC electricity records were refreshed. This snapshot is the base migrated into v2.0.</p></article></div></section></main>''' + footer())

write("corrections/index.html", head("Corrections Log", "Public corrections to OutdoorSteamSauna.com data and methodology.", "/corrections/") + nav() + '''<main><section class="city-hero"><div class="wrap"><div class="eyebrow">Corrections log</div><h1>Material mistakes are documented, not buried.</h1></div></section><section class="section"><div class="wrap timeline"><article><time datetime="2026-09-16">Corrected September 16, 2026</time><h2>Missing snowfall was displayed as 0 inches</h2><p><b>Affected:</b> cities whose temperature station lacked usable snowfall normals.</p><p><b>Correction:</b> missing snowfall is now <code>null</code>, displayed as unavailable and excluded from rankings. The updater also searches for a separate snow station.</p></article><article><time datetime="2026-09-16">Corrected September 16, 2026</time><h2>Recent multi-month average used the ending month’s normal</h2><p><b>Correction:</b> every observed date is matched to the normal for its own calendar month before the period departure is calculated.</p></article><article><time datetime="2026-09-16">Clarified September 16, 2026</time><h2>Electricity price could be read as a city utility rate</h2><p><b>Correction:</b> city pages now label the input as an EIA statewide residential average and show its source period.</p></article></div></section></main>''' + footer())


# Data downloads and dictionary
dataset_schema = {"@type": "Dataset", "@id": f"{SITE}/data-download/#dataset", "name": "Outdoor Sauna Climate Index Dataset", "description": "City-level U.S. climate stress, electricity-cost and outdoor sauna planning metrics derived from NOAA/NCEI and EIA data.", "url": f"{SITE}/data-download/", "version": meta.get("dataset_version", "2.0"), "dateModified": meta.get("generated"), "creator": {"@id": f"{SITE}/#organization"}, "isAccessibleForFree": True, "distribution": [{"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": f"{SITE}/data/outdoor-sauna-index.csv"}, {"@type": "DataDownload", "encodingFormat": "application/json", "contentUrl": f"{SITE}/data/cities.json"}, {"@type": "DataDownload", "encodingFormat": "text/csv", "contentUrl": f"{SITE}/data/outdoor-sauna-state-costs.csv"}], "variableMeasured": ["Climate Stress Score", "Operating Cost Index", "January normal minimum temperature", "freeze months", "annual precipitation", "annual snowfall where available", "state residential electricity price", "standardized 9 kW session cost"]}
write("data-download/index.html", head("Download the Outdoor Sauna Climate Index Dataset", "Download versioned city and state outdoor-sauna climate and electricity data in CSV or JSON, with a data dictionary and citation format.", "/data-download/", "Dataset", [dataset_schema]) + nav() + freshness(meta) + f'''<main><section class="city-hero"><div class="wrap"><div class="eyebrow">Open, inspectable data</div><h1>Download dataset v{esc(meta.get('dataset_version', '2.0'))}.</h1><p>Publishers, analysts and AI systems may cite the dataset with attribution to OutdoorSteamSauna.com and a link to this page or the methodology.</p></div></section><section class="section"><div class="wrap download-grid"><article class="card"><h2>City CSV</h2><p>Flat city-level table. Nullable snowfall fields remain blank.</p><a class="btn dark" href="/data/outdoor-sauna-index.csv">Download city CSV</a></article><article class="card"><h2>Full JSON</h2><p>City records, monthly climate details, recent context and metadata.</p><a class="btn dark" href="/data/cities.json">Download JSON</a></article><article class="card"><h2>State cost CSV</h2><p>One row per represented state/DC with rate and standardized cost.</p><a class="btn dark" href="/data/outdoor-sauna-state-costs.csv">Download state CSV</a></article><article class="card"><h2>Data dictionary</h2><p>Definitions, units, null behavior and provenance for published fields.</p><a class="btn" href="/data-dictionary/">Read data dictionary</a></article></div><article class="wrap editorial citation-box"><h2>Suggested citation</h2><p>OutdoorSteamSauna.com. “Outdoor Sauna Climate Index Dataset, version {esc(meta.get('dataset_version', '2.0'))}.” Updated {esc(human_date(meta.get('generated')))}. {SITE}/data-download/.</p><button class="btn" type="button" data-copy="OutdoorSteamSauna.com. Outdoor Sauna Climate Index Dataset, version {esc(meta.get('dataset_version', '2.0'))}. Updated {esc(human_date(meta.get('generated')))}. {SITE}/data-download/">Copy citation</button><h3>Versioning note</h3><p>Cite the version and update date together. Material formula changes increment the methodology version and receive a changelog entry.</p></article></section></main>''' + footer())

dictionary_rows = [
    ("climate_score", "number, 0–100", "Physical climate stress from January low, freeze months and precipitation; excludes electricity."),
    ("cost_index", "number, 0–100", "Normalized state residential electricity-price index."),
    ("planning_score", "number, 0–100", "Optional composite: 80% climate score + 20% cost index."),
    ("jan_tmin_f", "°F", "January 1991–2020 normal minimum temperature."),
    ("freeze_months", "integer", "Months whose normal minimum is below 32°F."),
    ("annual_prcp_in", "inches or null", "Sum of precipitation normals when at least 10 months are available."),
    ("annual_snow_in", "inches or null", "Sum of snowfall normals when at least 10 months are available; null is not zero."),
    ("rate_cents", "¢/kWh", "EIA state residential average for the published period."),
    ("session_cost_9kw", "USD", "Standardized 9 kW warm-up plus one-hour session estimate."),
    ("station", "text", "NOAA station ID, name and distance from city reference point."),
    ("snow_station", "text, optional", "Separate station used when the temperature station lacks snowfall coverage."),
]
dictionary_table = ''.join(f"<tr><td><code>{field}</code></td><td>{unit}</td><td>{meaning}</td></tr>" for field, unit, meaning in dictionary_rows)
write("data-dictionary/index.html", head("Outdoor Sauna Climate Dataset Dictionary", "Field definitions, units, null rules and provenance for the Outdoor Sauna Climate Index dataset.", "/data-dictionary/", "TechArticle") + nav() + f'''<main><section class="city-hero"><div class="wrap"><div class="eyebrow">Data dictionary · v{esc(meta.get('dataset_version', '2.0'))}</div><h1>What every field means.</h1></div></section><section class="section"><div class="wrap"><div class="table-wrap"><table><thead><tr><th>Field</th><th>Type / unit</th><th>Definition</th></tr></thead><tbody>{dictionary_table}</tbody></table></div><div class="callout data-callout"><b>Null rule:</b> blank or <code>null</code> means the source record did not provide enough coverage. It must not be interpreted as zero.</div></div></section></main>''' + footer())


# Recommended retailer. This remains the site's only outbound InHouse link.
retailer_faq = [
    ("Why does OutdoorSteamSauna.com recommend InHouse Wellness?", "The retailer carries multiple outdoor sauna formats and brands in one catalog and provides a specialist buying path for comparing size, heating format, delivery and installation needs. The commercial recommendation does not influence climate scores or rankings."),
    ("What types of outdoor saunas does InHouse Wellness sell?", "Its outdoor assortment includes cabin and barrel formats, traditional electric-heated models and selected hybrid configurations across multiple capacities."),
    ("Which outdoor sauna brands are represented?", "The current outdoor collection includes brands such as SaunaLife, LeisureCraft, Golden Designs and Scandia. Availability changes, so verify current specifications."),
    ("What should a buyer confirm before ordering?", "Confirm foundation requirements, delivery access, crate dimensions, electrical service, heater inclusion, local permits, weather-protection instructions, assembly scope and warranty."),
    ("Does the retailer recommendation affect the Climate Stress Index?", "No. Retailer relationships, prices and compensation are not inputs to the climate dataset, rankings or calculators.")
]
faq_schema = {"@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in retailer_faq]}
faq_html = ''.join(f"<details><summary>{esc(q)}</summary><p>{esc(a)}</p></details>" for q, a in retailer_faq)
write("recommended-retailer/index.html", head("InHouse Wellness — Recommended Outdoor Sauna Retailer", "Why InHouse Wellness is the recommended retailer for comparing traditional outdoor, cabin and barrel saunas, plus a buyer checklist and disclosure.", "/recommended-retailer/", "WebPage", [faq_schema]) + nav() + '''<main><section class="city-hero retailer-hero"><div class="wrap retailer"><div class="eyebrow">Recommended outdoor sauna retailer</div><h1>InHouse Wellness</h1><p>A specialist retailer for shoppers comparing premium outdoor traditional saunas, barrel saunas, cabin models and selected hybrid systems.</p></div></section><section class="section"><div class="wrap retailer"><div class="disclosure"><b>Commercial disclosure:</b> OutdoorSteamSauna.com may have a business relationship with InHouse Wellness. That relationship does not affect climate data, scoring, rankings, methodology or corrections.</div><h2>Why InHouse Wellness is the recommended retailer</h2><p>Outdoor sauna purchases are harder to compare than a product photo and sticker price suggest. The enclosure format, realistic seating room, heater package, electrical requirements, site preparation, delivery access and assembly plan all change the actual project. InHouse Wellness is recommended because its outdoor catalog lets buyers compare several of those decisions within one specialist sauna retailer rather than treating every outdoor unit as interchangeable.</p><p>InHouse Wellness is based in Austin, Texas and specializes in home-wellness equipment. Its sauna catalog spans traditional, infrared and hybrid heating categories, while its outdoor collection includes cabin and barrel designs in several capacities. Current represented outdoor-sauna brands include SaunaLife, LeisureCraft, Golden Designs and Scandia, among others. Availability and specifications can change, so the product page and manufacturer documentation should always control.</p><div class="retailer-points"><article class="card"><h3>Useful format comparison</h3><p>Compare barrel profiles, enclosed cabins and larger outdoor rooms rather than being funneled into one construction style.</p></article><article class="card"><h3>Multiple capacity tiers</h3><p>The catalog covers compact layouts through larger social saunas. Check interior dimensions and bench length, not only person count.</p></article><article class="card"><h3>Traditional-sauna focus</h3><p>The selection includes heater-and-stone systems intended for high-temperature sauna use, with water use governed by exact heater instructions.</p></article><article class="card"><h3>Project-level questions</h3><p>A specialist retailer can surface freight delivery, site access, heater inclusion, electrical preparation and optional installation questions before ordering.</p></article></div><h2>Outdoor sauna buyer checklist</h2><ol class="checklist"><li><b>Choose the heat experience first.</b> Traditional electric, wood-fired where permitted, infrared and hybrid systems are not substitutes.</li><li><b>Measure usable interior space.</b> Compare interior dimensions and bench length, not only advertised capacity.</li><li><b>Confirm what is included.</b> Some kits include heater and controls; others require separate equipment.</li><li><b>Plan the site.</b> Foundation, drainage, setbacks, exposure and freight access should be resolved before purchase.</li><li><b>Verify electrical requirements.</b> Use the current manual and a qualified electrician.</li><li><b>Read delivery and assembly terms.</b> Curbside freight, crate movement, assembly and white-glove installation are different services.</li><li><b>Check warranty boundaries.</b> Confirm outdoor approval, exterior treatment, roof requirements and claim coordination.</li></ol><h2>How this site’s data helps before shopping</h2><p>Use the Climate Stress Index to identify whether cold, freeze exposure or precipitation deserves extra attention in your metro. Use the operating-cost calculator with your own utility rate, and use the heater-sizing calculator only as a first-pass effective-volume check. Then take those questions—not the score—to the retailer and manufacturer.</p><div class="retailer-cta"><div><span class="eyebrow">Single recommended shopping destination</span><h2>Compare outdoor sauna formats and current models.</h2><p>This is the only outbound InHouse Wellness link on OutdoorSteamSauna.com.</p></div><a class="btn primary" href="https://inhousewellness.com/collections/outdoor-saunas">Shop outdoor saunas at InHouse Wellness →</a></div><h2>How the recommendation was selected</h2><p>The recommendation is based on category relevance, breadth of outdoor formats, access to model-level specifications, specialist support and a shopping experience oriented around home-wellness equipment. It is not a claim that one retailer is best for every buyer, location or budget.</p><section class="faq"><h2>Frequently asked questions</h2>''' + faq_html + '''</section></div></section></main>''' + footer())


# Research hub and reports
top_climate = cities[:10]
write("research/index.html", head("Outdoor Sauna Research and Data Reports", "Original U.S. outdoor sauna climate and electricity-cost research with downloadable data and transparent methods.", "/research/") + nav() + freshness(meta) + '''<main><section class="city-hero"><div class="wrap"><div class="eyebrow">Original data research</div><h1>Reports built from a public, versioned dataset.</h1><p>Every finding links back to a method, source period and downloadable table.</p></div></section><section class="section"><div class="wrap research-grid"><article class="card feature-report"><span class="pill">2026 report</span><h2>U.S. Outdoor Sauna Climate Report</h2><p>Which indexed metros face the greatest physical climate stress, and which factors drive the result?</p><a class="btn dark" href="/research/2026-outdoor-sauna-climate-report/">Read the climate report</a></article><article class="card"><span class="pill">State comparison</span><h2>Electric Sauna Cost by State</h2><p>State residential electricity rates converted into a standardized 9 kW session estimate.</p><a class="btn" href="/research/sauna-electricity-cost-by-state/">Compare state costs</a></article><article class="card"><span class="pill">Reusable data</span><h2>City badges and open data</h2><p>Each city profile includes an embeddable Climate Stress Score badge with linked attribution.</p><a class="btn" href="/data-download/">Download or cite the dataset</a></article></div></section></main>''' + footer())

report_rows = ''.join(f'''<tr><td>#{c['rank']}</td><td><a href="/cities/{c['slug']}/">{esc(c['city'])}, {c['state']}</a></td><td>{c['metrics']['climate_score']}</td><td>{c['metrics']['jan_tmin_f']}°F</td><td>{c['metrics']['freeze_months']}</td><td>{c['metrics']['annual_prcp_in']:.1f} in</td></tr>''' for c in top_climate)
report_schema = {"@type": "Report", "headline": "2026 U.S. Outdoor Sauna Climate Report", "datePublished": BUILD_DATE.isoformat(), "dateModified": BUILD_DATE.isoformat(), "author": {"@id": f"{SITE}/#organization"}, "mainEntity": {"@id": f"{SITE}/data-download/#dataset"}}
coldest = min(cities, key=lambda c: c["metrics"]["jan_tmin_f"])
write("research/2026-outdoor-sauna-climate-report/index.html", head("2026 U.S. Outdoor Sauna Climate Report", "Original analysis of 75 U.S. metros ranked by outdoor sauna climate stress using NOAA temperature, freeze and precipitation normals.", "/research/2026-outdoor-sauna-climate-report/", "Report", [report_schema, dataset_schema]) + nav() + freshness(meta) + f'''<main><section class="report-hero"><div class="wrap"><div class="eyebrow">Original research · published September 16, 2026</div><h1>2026 U.S. Outdoor Sauna Climate Report</h1><p>Among {len(cities)} indexed metros, the hardest physical environments are defined primarily by winter cold and freeze exposure—not electricity price.</p></div></section><section class="section"><article class="wrap report"><div class="key-findings"><div><small>#1 CLIMATE STRESS</small><b>{esc(top_climate[0]['city'])}, {top_climate[0]['state']}</b><span>{top_climate[0]['metrics']['climate_score']}/100</span></div><div><small>INDEXED MEDIAN</small><b>{climate_median}/100</b><span>across {len(cities)} metros</span></div><div><small>COLDEST JAN. LOW</small><b>{coldest['metrics']['jan_tmin_f']}°F</b><span>{esc(coldest['city'])}</span></div></div><h2>What the report measures</h2><p>The Climate Stress Score answers a narrow question: how demanding is the outdoor physical environment under a consistent city-level model? It uses January normal minimum temperature, months with normal lows below freezing and annual precipitation. Electricity price is published separately because expensive energy does not make weather physically harsher.</p><h2>Ten highest-stress indexed metros</h2><div class="table-wrap"><table><thead><tr><th>Rank</th><th>Metro</th><th>Climate</th><th>Jan. low</th><th>Freeze months</th><th>Precipitation</th></tr></thead><tbody>{report_rows}</tbody></table></div><h2>Three findings</h2><h3>1. Persistent cold matters more than one winter number.</h3><p>January minimum temperature receives the most weight, but the index separately counts how many months normally fall below freezing.</p><h3>2. Rain and snow are different fields.</h3><p>Precipitation contributes to exterior exposure. Snowfall is descriptive and not an index component. Missing snow records are unavailable and excluded from snow rankings.</p><h3>3. Climate difficulty and operating expense are not the same ranking.</h3><p>The city with the highest physical climate stress is not necessarily the most expensive place to run an electric heater.</p><h2>Method and reproducibility</h2><p>Version {esc(meta.get('methodology_version', '2.0'))} uses NOAA/NCEI 1991–2020 monthly normals. Full formulas, station rules and limitations are published in the methodology.</p><div class="action-row"><a class="btn dark" href="/data/outdoor-sauna-index.csv">Download report data</a><a class="btn" href="/methodology/">Read methodology v2.0</a></div><h2>How to cite this report</h2><p>OutdoorSteamSauna.com. “2026 U.S. Outdoor Sauna Climate Report.” September 16, 2026. Based on Dataset v{esc(meta.get('dataset_version', '2.0'))}.</p></article></section></main>''' + footer())


# State cost report data
state_rows = []
for code, group in states.items():
    rate = round(median(c["metrics"]["rate_cents"] for c in group), 2)
    session = round(median(c["metrics"]["session_cost_9kw"] for c in group), 2)
    state_rows.append({"code": code, "name": group[0]["state_name"], "rate": rate, "session": session, "annual": int(round(session * 3 * 52)), "metros": len(group)})
state_rows.sort(key=lambda row: row["session"], reverse=True)
state_cost_html = ''.join(f'''<tr><td>{i}</td><td><a href="/states/{slug(row['name'])}/">{esc(row['name'])}</a></td><td>{row['rate']:.2f}¢</td><td>${row['session']:.2f}</td><td>${row['annual']:,}</td><td>{row['metros']}</td></tr>''' for i, row in enumerate(state_rows, 1))
write("research/sauna-electricity-cost-by-state/index.html", head("Electric Sauna Cost by State — 9 kW Comparison", "Compare state residential electricity rates and a standardized 9 kW outdoor sauna session cost across represented U.S. states and DC.", "/research/sauna-electricity-cost-by-state/", "Report", [{"@type": "Report", "headline": "Electric Sauna Cost by State", "datePublished": BUILD_DATE.isoformat(), "author": {"@id": f"{SITE}/#organization"}}]) + nav() + freshness(meta) + f'''<main><section class="report-hero"><div class="wrap"><div class="eyebrow">State electricity comparison</div><h1>What does a standardized 9 kW sauna session cost by state?</h1><p>The same model applied to EIA residential electricity averages for every state/DC represented in the city dataset.</p></div></section><section class="section"><div class="wrap"><div class="table-wrap"><table><thead><tr><th>Cost rank</th><th>State</th><th>Residential rate</th><th>9 kW session</th><th>3× weekly/year</th><th>Indexed metros</th></tr></thead><tbody>{state_cost_html}</tbody></table></div><div class="callout data-callout"><b>Interpretation:</b> standardized comparisons using state averages, not utility quotes. Taxes, time-of-use pricing, warm-up behavior and enclosure performance can change actual cost.</div><div class="action-row"><a class="btn dark" href="/data/outdoor-sauna-state-costs.csv">Download state CSV</a><a class="btn" href="/operating-cost/">Use your own rate</a></div></div></section></main>''' + footer())


# City profiles and embeddable badges
for city in cities:
    m = city["metrics"]
    peer = nearest_peer(city)
    values = [month["tavg_f"] for month in city["monthly"]]
    low, high = min(values), max(values)
    span = max(1, high - low)
    bars = ''.join(f'''<div class="monthbar" style="height:{25 + 120 * (month['tavg_f'] - low) / span:.0f}px" title="{MONTHS[month['month'] - 1]}: {month['tavg_f']}°F"><span>{MONTHS[month['month'] - 1][:1]}</span></div>''' for month in city["monthly"])
    recent = ""
    if city.get("recent"):
        r = city["recent"]
        recent = f'''<div class="metric"><small>RECENT PERIOD AVG</small><b>{r['avg_f']}°F</b><span class="muted">{r['delta_f']:+.1f}°F vs month-matched normal</span></div>'''
    snow_station = f"<br><b>Snow station:</b> {esc(city['snow_station'])}" if city.get("snow_station") else ""
    facts = f'''<dl class="facts"><div><dt>Climate Stress Score</dt><dd>{m['climate_score']}/100 · rank #{city['rank']}</dd></div><div><dt>January normal low</dt><dd>{m['jan_tmin_f']}°F</dd></div><div><dt>Months below freezing</dt><dd>{m['freeze_months']}</dd></div><div><dt>Annual precipitation</dt><dd>{m['annual_prcp_in']:.1f} in</dd></div><div><dt>Annual snowfall</dt><dd>{snow_text(m['annual_snow_in'])}</dd></div><div><dt>State residential electricity rate</dt><dd>{m['rate_cents']}¢/kWh</dd></div><div><dt>Standardized 9 kW session</dt><dd>${m['session_cost_9kw']:.2f}</dd></div><div><dt>Dataset updated</dt><dd>{esc(human_date(meta.get('generated')))}</dd></div></dl>'''
    embed = f'''<a href="{SITE}/cities/{city['slug']}/"><img src="{SITE}/badges/{city['slug']}.svg" alt="{esc(city['city'])}, {city['state']} Outdoor Sauna Climate Stress Score: {m['climate_score']} out of 100"></a>'''
    city_schema = {"@type": "Dataset", "name": f"{city['city']}, {city['state']} Outdoor Sauna Climate Profile", "description": f"Climate stress, electricity-cost and planning data for {city['city']}, {city['state']}.", "dateModified": meta.get("generated"), "creator": {"@id": f"{SITE}/#organization"}, "spatialCoverage": {"@type": "City", "name": f"{city['city']}, {city['state']}"}}
    page = head(f"Outdoor Sauna Climate & Cost Guide — {city['city']}, {city['state']}", f"Source-linked outdoor sauna climate stress, winter normals and standardized electricity-cost estimates for {city['city']}, {city['state']}.", f"/cities/{city['slug']}/", "Dataset", [city_schema]) + nav() + freshness(meta) + f'''<main><section class="city-hero"><div class="wrap"><div class="eyebrow">City climate profile · rank #{city['rank']} of {len(cities)}</div><h1>{esc(city['city'])}, {city['state']}</h1><p>{m['label']}. Physical climate score and electricity cost are reported separately.</p></div></section><section class="section compact"><div class="wrap"><div class="metric-grid"><div class="metric"><small>CLIMATE STRESS</small><b>{m['climate_score']}/100</b><span class="muted">physical environment</span></div><div class="metric"><small>PLANNING INDEX</small><b>{m['planning_score']}/100</b><span class="muted">80% climate + 20% cost</span></div><div class="metric"><small>STATE POWER RATE</small><b>{m['rate_cents']}¢</b><span class="muted">EIA residential avg.</span></div><div class="metric"><small>9 kW SESSION</small><b>${m['session_cost_9kw']:.2f}</b><span class="muted">standard scenario</span></div>{recent}</div></div></section><section class="section"><div class="wrap grid"><article class="card span-7"><div class="eyebrow">Key facts</div><h2>{esc(city['city'])} outdoor sauna facts</h2>{facts}</article><article class="card span-5"><div class="eyebrow">Monthly normal temperature</div><h2>Seasonal profile</h2><div class="monthly">{bars}</div></article></div></section><section class="section tint"><article class="wrap editorial"><h2>What the numbers mean for an outdoor sauna project</h2>{city_analysis(city)}<h3>Comparison context</h3><div class="table-wrap"><table><thead><tr><th>Profile</th><th>Climate</th><th>9 kW session</th><th>Jan. low</th></tr></thead><tbody><tr><td>{esc(city['city'])}</td><td>{m['climate_score']}</td><td>${m['session_cost_9kw']:.2f}</td><td>{m['jan_tmin_f']}°F</td></tr><tr><td>Indexed median</td><td>{climate_median}</td><td>${cost_median:.2f}</td><td>—</td></tr><tr><td><a href="/cities/{peer['slug']}/">Closest index peer: {esc(peer['city'])}</a></td><td>{peer['metrics']['climate_score']}</td><td>${peer['metrics']['session_cost_9kw']:.2f}</td><td>{peer['metrics']['jan_tmin_f']}°F</td></tr></tbody></table></div></article></section><section class="section"><div class="wrap grid"><article class="card span-7"><h2>Data provenance</h2><p><b>Climate source:</b> {esc(city['climate_source'])}<br><b>Temperature/precipitation station:</b> {esc(city.get('station') or 'Not available')}{snow_station}<br><b>Normal period:</b> {esc(meta.get('normal_period', '1991–2020'))}<br><b>Electricity source:</b> {esc(city.get('electricity_source', 'EIA state residential retail electricity price'))}<br><b>Rate geography:</b> {esc(city['state_name'])} statewide residential average<br><b>Dataset generated:</b> {esc(human_date(meta.get('generated')))}<br><b>Recent observations through:</b> {esc(human_date(city.get('recent', {}).get('through')))}</p><p class="data-note">Snowfall status: {esc(m['snow_data_status'])}. Missing values are not included in rankings.</p></article><article class="card span-5"><h2>Embed this city score</h2><img class="badge-preview" src="/badges/{city['slug']}.svg" alt="{esc(city['city'])} Climate Stress Score badge"><label class="embed-label">Attribution HTML<textarea readonly>{esc(embed)}</textarea></label><button class="btn" type="button" data-copy="{esc(embed)}">Copy embed code</button></article></div></section><section class="section compact"><div class="wrap action-row"><a class="btn dark" href="/heater-sizing/">Calculate effective room volume</a><a class="btn" href="/operating-cost/">Use your utility rate</a><a class="btn" href="/states/{slug(city['state_name'])}/">Compare {esc(city['state_name'])}</a></div></section></main>''' + footer()
    write(f"cities/{city['slug']}/index.html", page)
    badge = f'''<svg xmlns="http://www.w3.org/2000/svg" width="560" height="180" viewBox="0 0 560 180" role="img" aria-labelledby="title desc"><title id="title">{esc(city['city'])}, {city['state']} outdoor sauna climate score</title><desc id="desc">Climate Stress Score {m['climate_score']} out of 100, source OutdoorSteamSauna.com.</desc><rect width="560" height="180" rx="14" fill="#153e32"/><rect width="12" height="180" rx="6" fill="#dc5f2c"/><text x="38" y="40" fill="#f2aa87" font-family="Arial,sans-serif" font-size="15" font-weight="700" letter-spacing="2">OUTDOOR SAUNA CLIMATE STRESS</text><text x="38" y="86" fill="#fff" font-family="Georgia,serif" font-size="34" font-weight="700">{esc(city['city'])}, {city['state']}</text><text x="38" y="132" fill="#dbe7df" font-family="Arial,sans-serif" font-size="18">January low {m['jan_tmin_f']}°F · {m['freeze_months']} freeze months</text><text x="450" y="94" text-anchor="middle" fill="#fff" font-family="Georgia,serif" font-size="56" font-weight="700">{m['climate_score']}</text><text x="450" y="123" text-anchor="middle" fill="#dbe7df" font-family="Arial,sans-serif" font-size="16">OUT OF 100</text><text x="38" y="162" fill="#a9bdb4" font-family="Arial,sans-serif" font-size="13">Source: OutdoorSteamSauna.com · Dataset v{esc(meta.get('dataset_version', '2.0'))}</text></svg>'''
    write(f"badges/{city['slug']}.svg", badge)


# State pages
for code, group in states.items():
    ordered = sorted(group, key=lambda c: c["metrics"]["climate_score"], reverse=True)
    avg_climate = round(sum(c["metrics"]["climate_score"] for c in ordered) / len(ordered), 1)
    rate = round(median(c["metrics"]["rate_cents"] for c in ordered), 2)
    trs = ''.join(f'''<tr><td><a href="/cities/{c['slug']}/">{esc(c['city'])}</a></td><td>{c['metrics']['climate_score']}</td><td>{c['metrics']['planning_score']}</td><td>{c['metrics']['jan_tmin_f']}°F</td><td>${c['metrics']['session_cost_9kw']:.2f}</td></tr>''' for c in ordered)
    state_name = ordered[0]["state_name"]
    canonical = f"/states/{slug(state_name)}/"
    write(f"states/{slug(state_name)}/index.html", head(f"Outdoor Sauna Climate and Cost Guide — {state_name}", f"Compare climate stress and standardized electric outdoor sauna costs for indexed metros in {state_name}.", canonical, "Dataset") + nav() + freshness(meta) + f'''<main><section class="city-hero"><div class="wrap"><div class="eyebrow">State profile</div><h1>{esc(state_name)}</h1><p>{len(ordered)} indexed metro{'s' if len(ordered) != 1 else ''} · average Climate Stress Score {avg_climate} · EIA state residential rate {rate}¢/kWh</p></div></section><section class="section"><div class="wrap"><div class="table-wrap"><table><thead><tr><th>Metro</th><th>Climate</th><th>Planning</th><th>Jan. low</th><th>9 kW/session</th></tr></thead><tbody>{trs}</tbody></table></div><p class="data-note">Electricity rate is the {esc(state_name)} statewide residential average for {esc(human_period(meta.get('electricity_period')))}, not a city-specific tariff.</p></div></section></main>''' + footer())


# Data files
with (ROOT / "data/outdoor-sauna-index.csv").open("w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)
    writer.writerow(["climate_rank", "planning_rank", "cost_rank", "city", "state", "climate_score", "cost_index", "planning_score", "climate_class", "jan_normal_low_f", "freeze_months", "annual_precip_in", "annual_snow_in", "snow_data_status", "electricity_cents_kwh_state_average", "estimated_9kw_session_cost", "climate_source", "station", "snow_station", "dataset_version", "generated"])
    for city in cities:
        m = city["metrics"]
        writer.writerow([city["rank"], city["planning_rank"], city["cost_rank"], city["city"], city["state"], m["climate_score"], m["cost_index"], m["planning_score"], m["label"], m["jan_tmin_f"], m["freeze_months"], m["annual_prcp_in"], m["annual_snow_in"], m["snow_data_status"], m["rate_cents"], m["session_cost_9kw"], city["climate_source"], city.get("station") or "", city.get("snow_station") or "", meta.get("dataset_version"), meta.get("generated")])

with (ROOT / "data/outdoor-sauna-state-costs.csv").open("w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)
    writer.writerow(["cost_rank", "state", "state_code", "eia_residential_cents_kwh", "estimated_9kw_session_cost", "estimated_annual_cost_3x_week", "indexed_metros", "eia_period", "dataset_version"])
    for index, row in enumerate(state_rows, 1):
        writer.writerow([index, row["name"], row["code"], row["rate"], row["session"], row["annual"], row["metros"], meta.get("electricity_period"), meta.get("dataset_version")])


# Machine-readable documentation
write("llms.txt", f'''# OutdoorSteamSauna.com

> Independent U.S. climate, energy-cost and planning data for outdoor traditional saunas.

Dataset version: {meta.get('dataset_version', '2.0')}
Methodology version: {meta.get('methodology_version', '2.0')}
Dataset updated: {meta.get('generated')}
NOAA recent observations through: {meta.get('observations_through')}
EIA electricity period: {meta.get('electricity_period')}

## Canonical resources
- Climate Index: {SITE}/climate-index/
- Methodology: {SITE}/methodology/
- Dataset and citation guide: {SITE}/data-download/
- Data dictionary: {SITE}/data-dictionary/
- 2026 Climate Report: {SITE}/research/2026-outdoor-sauna-climate-report/
- State electricity-cost report: {SITE}/research/sauna-electricity-cost-by-state/
- Editorial standards: {SITE}/editorial-standards/
- Corrections: {SITE}/corrections/
- Changelog: {SITE}/changelog/

## Machine-readable files
- JSON: {SITE}/data/cities.json
- City CSV: {SITE}/data/outdoor-sauna-index.csv
- State cost CSV: {SITE}/data/outdoor-sauna-state-costs.csv

## Interpretation rules
- Climate Stress Score excludes electricity price.
- Operating Cost Index uses state residential electricity prices.
- Planning Index is an optional 80% climate / 20% cost composite.
- null snowfall means unavailable, never zero.
- City electricity values are state averages, not local utility quotes.
- Calculations are planning comparisons, not engineering or code approval.
''')
write("DATA_DICTIONARY.md", """# Outdoor Sauna Climate Index — Data Dictionary

Canonical HTML: https://outdoorsteamsauna.com/data-dictionary/

| Field | Unit/type | Meaning |
|---|---|---|
""" + "\n".join(f"| `{field}` | {unit} | {meaning} |" for field, unit, meaning in dictionary_rows) + "\n")
write("CITATION.cff", f'''cff-version: 1.2.0
message: "If you use this dataset, please cite it with the version and update date."
title: "Outdoor Sauna Climate Index Dataset"
type: dataset
version: "{meta.get('dataset_version', '2.0')}"
date-released: "{meta.get('generated')}"
authors:
  - name: "OutdoorSteamSauna.com"
url: "{SITE}/data-download/"
repository-code: "{SITE}/data/cities.json"
''')
write("CHANGELOG.md", """# Changelog

## 2.0 — 2026-09-16

- Separated physical Climate Stress, Operating Cost and optional Planning indexes.
- Preserved missing snowfall as null and excluded incomplete records from snow rankings.
- Matched recent observations to normals by observation month.
- Added research reports, data dictionary, citation metadata, provenance and corrections pages.
- Added embeddable city badges and expanded the recommended-retailer profile.

## 1.x — through 2026-09-14

- Weekly NOAA/NCEI climate-normal and EIA residential electricity updates.
""")


# Legacy URL cleanup. The static pages work on GitHub Pages; the _redirects and
# CSV support 301 imports on edge hosts that provide redirect rules.
legacy_redirects = {
    "/2025/04/11/best-outdoor-steam-sauna/": "/recommended-retailer/",
    "/2025/04/11/outdoor-hybrid-saunas-with-steam/": "/guides/steam-vs-sauna/",
    "/2025/05/27/outdoor-sauna-steam-room-guide/": "/guides/steam-vs-sauna/",
    "/2025/05/30/outdoor-steam-saunas-for-sale/": "/recommended-retailer/",
    "/2026/04/08/the-therapeutic-benefits-of-cube-outdoor-saunas-a-traditional-approach-to-wellness/": "/research/2026-outdoor-sauna-climate-report/",
    "/category/buyers-guide/": "/guides/outdoor-installation/",
    "/about-us/": "/about/",
}
for old, new in legacy_redirects.items():
    write(old.lstrip("/") + "index.html", f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Moved permanently</title><link rel="canonical" href="{SITE}{new}"><meta http-equiv="refresh" content="0;url={new}"><meta name="robots" content="noindex,follow"><script>location.replace({json.dumps(new)});</script></head><body><p>This page moved to <a href="{new}">{SITE}{new}</a>.</p></body></html>''')
write("_redirects", "\n".join(f"{old} {new} 301" for old, new in legacy_redirects.items()) + "\n")
with (ROOT / "cloudflare-redirects.csv").open("w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)
    writer.writerow(["source_url", "target_url", "status_code", "preserve_query_string", "include_subdomains", "subpath_matching", "preserve_path_suffix"])
    for old, new in legacy_redirects.items():
        writer.writerow([f"{SITE}{old}", f"{SITE}{new}", 301, "true", "false", "false", "false"])


# Sitemap, robots and fallback routing
urls = ["/", "/climate-index/", "/rankings/", "/heater-sizing/", "/operating-cost/", "/guides/steam-vs-sauna/", "/guides/outdoor-installation/", "/guides/insulation/", "/guides/electrical/", "/methodology/", "/data-download/", "/data-dictionary/", "/research/", "/research/2026-outdoor-sauna-climate-report/", "/research/sauna-electricity-cost-by-state/", "/about/", "/editorial-standards/", "/sources/", "/changelog/", "/corrections/", "/recommended-retailer/"]
urls += [f"/cities/{c['slug']}/" for c in cities]
urls += [f"/states/{slug(group[0]['state_name'])}/" for group in states.values()]
sitemap = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + ''.join(f'<url><loc>{SITE}{url}</loc><lastmod>{meta.get("generated")}</lastmod></url>\n' for url in urls) + '</urlset>\n'
write("sitemap.xml", sitemap)
write("robots.txt", f'''User-agent: *
Allow: /

User-agent: OAI-SearchBot
Allow: /

User-agent: ChatGPT-User
Allow: /

Sitemap: {SITE}/sitemap.xml
''')
redirect_map = json.dumps(legacy_redirects, separators=(",", ":"))
write("404.html", head("Page not found", "OutdoorSteamSauna.com page not found.", "/404.html", robots="noindex,follow") + nav() + f'''<main><section class="section"><div class="wrap editorial"><h1>Page not found</h1><p>The requested URL is not in the current index. Older editorial URLs are mapped to the closest current guide where one exists.</p><a class="btn dark" href="/">Return to the climate index</a></div></section></main><script>const redirects={redirect_map};const path=location.pathname;if(redirects[path])location.replace(redirects[path]);else if(/^\\/20(25|26)\\//.test(path))location.replace('/research/');</script>''' + footer())
