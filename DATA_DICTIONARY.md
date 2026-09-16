# Outdoor Sauna Climate Index — Data Dictionary

Canonical HTML: https://outdoorsteamsauna.com/data-dictionary/

| Field | Unit/type | Meaning |
|---|---|---|
| `climate_score` | number, 0–100 | Physical climate stress from January low, freeze months and precipitation; excludes electricity. |
| `cost_index` | number, 0–100 | Normalized state residential electricity-price index. |
| `planning_score` | number, 0–100 | Optional composite: 80% climate score + 20% cost index. |
| `jan_tmin_f` | °F | January 1991–2020 normal minimum temperature. |
| `freeze_months` | integer | Months whose normal minimum is below 32°F. |
| `annual_prcp_in` | inches or null | Sum of precipitation normals when at least 10 months are available. |
| `annual_snow_in` | inches or null | Sum of snowfall normals when at least 10 months are available; null is not zero. |
| `rate_cents` | ¢/kWh | EIA state residential average for the published period. |
| `session_cost_9kw` | USD | Standardized 9 kW warm-up plus one-hour session estimate. |
| `station` | text | NOAA station ID, name and distance from city reference point. |
| `snow_station` | text, optional | Separate station used when the temperature station lacks snowfall coverage. |
