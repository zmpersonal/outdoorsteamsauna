from pathlib import Path
import os, json, re, math, csv, io, time
from datetime import date, timedelta
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/cities.json'

S = requests.Session()
S.headers.update({
    'User-Agent': 'OutdoorSteamSauna.com climate index updater / NOAA NCEI public-data research'
})

NORMALS_DIR = 'https://www.ncei.noaa.gov/data/normals-monthly/1991-2020/access/'
GHCND_STATIONS = 'https://www.ncei.noaa.gov/pub/data/ghcn/daily/ghcnd-stations.txt'
ACCESS_API = 'https://www.ncei.noaa.gov/access/services/data/v1'


def clamp(x, a, b):
    return max(a, min(b, x))


def num(v):
    try:
        s = str(v).strip()
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def month_num(rec):
    for k in ('month', 'MONTH'):
        if k in rec and str(rec[k]).strip():
            try:
                return int(str(rec[k]).strip())
            except Exception:
                pass

    s = str(rec.get('DATE', '')).strip()
    if re.fullmatch(r'0?[1-9]|1[0-2]', s):
        return int(s)

    m = re.search(r'(?:^|[-/])(0?[1-9]|1[0-2])(?:$|[-/])', s)
    return int(m.group(1)) if m else None


def haversine_miles(lat1, lon1, lat2, lon2):
    r = 3958.7613
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def fetch_eia(api_key):
    url = 'https://api.eia.gov/v2/electricity/retail-sales/data/'
    params = [
        ('api_key', api_key),
        ('frequency', 'monthly'),
        ('data[]', 'price'),
        ('facets[sectorid][]', 'RES'),
        ('sort[0][column]', 'period'),
        ('sort[0][direction]', 'desc'),
        ('offset', '0'),
        ('length', '5000')
    ]
    r = S.get(url, params=params, timeout=45)
    r.raise_for_status()
    rows = r.json()['response']['data']
    out = {}
    for x in rows:
        st = x.get('stateid')
        p = num(x.get('price'))
        if st and len(st) == 2 and p is not None and st not in out:
            out[st] = {'price': p, 'period': x.get('period')}
    if len(out) < 45:
        raise RuntimeError(f'EIA returned only {len(out)} states/DC')
    return out


def fetch_normal_station_ids():
    """
    NCEI publishes one CSV per station in the 1991-2020 monthly normals archive.
    Reading the directory index lets us limit the GHCNd inventory to stations that
    actually have a normals file, rather than guessing station IDs.
    """
    r = S.get(NORMALS_DIR, timeout=60)
    r.raise_for_status()
    ids = set(re.findall(r'href=["\']([A-Za-z0-9_]{11})\.csv["\']', r.text))

    # The directory contains many precipitation-only citizen stations. For a
    # temperature-based city index, prefer NWS/WBAN and COOP stations.
    ids = {sid for sid in ids if sid.startswith(('USW', 'USC'))}

    if len(ids) < 1000:
        raise RuntimeError(
            f'NOAA normals station directory returned only {len(ids)} usable USW/USC stations'
        )
    return ids


def fetch_station_inventory(normal_ids):
    """
    Parse NOAA GHCN-Daily's fixed-width station inventory:
      ID, latitude, longitude, elevation, state, station name.
    Only retain stations that have a 1991-2020 monthly normals file.
    """
    r = S.get(GHCND_STATIONS, timeout=90)
    r.raise_for_status()

    stations = []
    for line in r.text.splitlines():
        if len(line) < 71:
            continue
        sid = line[0:11].strip()
        if sid not in normal_ids:
            continue
        try:
            lat = float(line[12:20])
            lon = float(line[21:30])
        except Exception:
            continue
        state = line[38:40].strip()
        name = line[41:71].strip()
        stations.append({
            'id': sid,
            'lat': lat,
            'lon': lon,
            'state': state,
            'name': name
        })

    if len(stations) < 1000:
        raise RuntimeError(f'Parsed only {len(stations)} NOAA stations with normals')
    return stations


_station_cache = {}


def fetch_station_normals(station):
    sid = station['id']
    if sid in _station_cache:
        return _station_cache[sid]

    url = f'{NORMALS_DIR}{sid}.csv'
    try:
        r = S.get(url, timeout=35)
        if r.status_code != 200:
            _station_cache[sid] = None
            return None

        rows = list(csv.DictReader(io.StringIO(r.text)))
        by = {}

        for row in rows:
            m = month_num(row)
            if not m or not (1 <= m <= 12):
                continue

            d = by.setdefault(m, {})
            for key, outkey in (
                ('MLY-TAVG-NORMAL', 'tavg_f'),
                ('MLY-TMIN-NORMAL', 'tmin_f'),
                ('MLY-TMAX-NORMAL', 'tmax_f'),
                ('MLY-PRCP-NORMAL', 'prcp_in'),
                ('MLY-SNOW-NORMAL', 'snow_in')
            ):
                v = num(row.get(key))
                if v is not None:
                    d[outkey] = v

        if len(by) < 12:
            _station_cache[sid] = None
            return None

        months = []
        temp_complete = 0
        prcp_complete = 0

        for m in range(1, 13):
            d = by.get(m, {})
            if 'tavg_f' not in d and 'tmin_f' in d and 'tmax_f' in d:
                d['tavg_f'] = (d['tmin_f'] + d['tmax_f']) / 2

            if all(k in d for k in ('tavg_f', 'tmin_f', 'tmax_f')):
                temp_complete += 1
            if 'prcp_in' in d:
                prcp_complete += 1

            if not all(k in d for k in ('tavg_f', 'tmin_f', 'tmax_f')):
                _station_cache[sid] = None
                return None

            months.append({
                'month': m,
                'tmin_f': round(d['tmin_f'], 1),
                'tmax_f': round(d['tmax_f'], 1),
                'tavg_f': round(d['tavg_f'], 1),
                'prcp_in': round(d.get('prcp_in', 0.0), 2),
                'snow_in': round(d.get('snow_in', 0.0), 1)
            })

        # Require complete temperature normals and useful precipitation coverage.
        if temp_complete != 12 or prcp_complete < 10:
            _station_cache[sid] = None
            return None

        result = {
            'station': sid,
            'station_name': station['name'],
            'station_lat': station['lat'],
            'station_lon': station['lon'],
            'monthly': months
        }
        _station_cache[sid] = result
        return result

    except Exception:
        _station_cache[sid] = None
        return None


def fetch_city_normals(city, stations):
    """
    Find the nearest NOAA station with a complete temperature-normal record.
    Try official stations in distance order rather than relying on the Access
    Service's bounding-box behavior for the normals dataset.
    """
    candidates = []
    for st in stations:
        # Prefer same-state stations when possible, but don't require it near borders.
        d = haversine_miles(city['lat'], city['lon'], st['lat'], st['lon'])
        if d <= 150:
            state_penalty = 0 if st['state'] == city['state'] else 15
            candidates.append((d + state_penalty, d, st))

    candidates.sort(key=lambda x: x[0])

    # 35 nearby official stations is plenty for major metros and gives us
    # fallback if the nearest station has precipitation-only or incomplete normals.
    for _, actual_distance, st in candidates[:35]:
        normal = fetch_station_normals(st)
        if normal:
            normal['distance_miles'] = round(actual_distance, 1)
            return normal

    return None


def fetch_recent(station, monthly):
    """
    Recent observations are optional freshness context. If NCEI's data service
    is delayed or unavailable, the long-term normals remain valid and the site
    still updates from EIA.
    """
    if not station:
        return None

    end = date.today() - timedelta(days=2)
    start = end - timedelta(days=32)
    params = {
        'dataset': 'daily-summaries',
        'stations': station,
        'startDate': start.isoformat(),
        'endDate': end.isoformat(),
        'dataTypes': 'TAVG,TMIN,TMAX',
        'format': 'json',
        'units': 'standard'
    }

    try:
        r = S.get(ACCESS_API, params=params, timeout=35)
        r.raise_for_status()
        rows = r.json()

        vals = []
        for x in rows:
            v = num(x.get('TAVG'))
            if v is None:
                lo = num(x.get('TMIN'))
                hi = num(x.get('TMAX'))
                v = (lo + hi) / 2 if lo is not None and hi is not None else None
            if v is not None:
                vals.append(v)

        if len(vals) < 10:
            return None

        avg = sum(vals) / len(vals)
        normal = monthly[end.month - 1]['tavg_f']
        return {
            'avg_f': round(avg, 1),
            'delta_f': round(avg - normal, 1),
            'days': len(vals),
            'through': end.isoformat()
        }
    except Exception:
        return None


def metrics(monthly, rate):
    jan = monthly[0]['tmin_f']
    annual_prcp = sum(x.get('prcp_in') or 0 for x in monthly)
    annual_snow = sum(x.get('snow_in') or 0 for x in monthly)
    freeze_months = sum(1 for x in monthly if x['tmin_f'] < 32)

    cold = clamp((45 - jan) / 55, 0, 1)
    freeze = clamp(freeze_months / 7, 0, 1)
    wet = clamp(annual_prcp / 70, 0, 1)
    energy = clamp((rate - 10) / 25, 0, 1)

    score = round(100 * (.48 * cold + .22 * freeze + .10 * wet + .20 * energy), 1)
    warm = .95 + .45 * cold
    kwh = 9 * (warm + .60)
    cost = round(kwh * rate / 100, 2)

    label = (
        'Severe planning load' if score >= 75 else
        'High planning load' if score >= 55 else
        'Moderate planning load' if score >= 35 else
        'Mild planning load'
    )

    return {
        'score': score,
        'label': label,
        'jan_tmin_f': round(jan, 1),
        'annual_prcp_in': round(annual_prcp, 1),
        'annual_snow_in': round(annual_snow, 1),
        'freeze_months': freeze_months,
        'rate_cents': round(rate, 2),
        'session_cost_9kw': cost,
        'annual_cost_3x_week': int(round(cost * 3 * 52))
    }


def main():
    key = os.environ.get('EIA_API_KEY', '').strip()
    if not key:
        raise RuntimeError(
            'EIA_API_KEY is not set. Add it under Settings > Secrets and variables > Actions.'
        )

    obj = json.load(open(DATA))
    cities = obj['cities']

    # Electricity rates
    eia = fetch_eia(key)
    print(f'EIA OK: {len(eia)} states/DC')

    # NOAA station discovery
    print('Loading NOAA 1991-2020 normals station directory...')
    normal_ids = fetch_normal_station_ids()
    print(f'NOAA normals directory: {len(normal_ids)} USW/USC station files')

    print('Loading NOAA GHCN station inventory...')
    stations = fetch_station_inventory(normal_ids)
    print(f'NOAA candidate stations: {len(stations)}')

    live = 0

    for idx, c in enumerate(cities, 1):
        n = fetch_city_normals(c, stations)

        if n:
            c['monthly'] = n['monthly']
            c['station'] = (
                f"{n['station']} — {n['station_name']} "
                f"({n['distance_miles']} mi from city reference point)"
            )
            c['climate_source'] = (
                'NOAA/NCEI U.S. Monthly Climate Normals 1991–2020 '
                '(direct by-station archive)'
            )
            c['recent'] = fetch_recent(n['station'], n['monthly'])
            live += 1
            print(
                f"[{idx}/{len(cities)}] {c['slug']}: NOAA OK — "
                f"{n['station']} / {n['distance_miles']} mi"
            )
        else:
            print(f"[{idx}/{len(cities)}] {c['slug']}: NOAA preserved — no complete nearby station")

        e = eia.get(c['state'])
        if e:
            c['electricity_source'] = (
                f"EIA residential retail electricity price ({e['period']})"
            )
            rate = e['price']
        else:
            rate = c['metrics']['rate_cents']

        c['metrics'] = metrics(c['monthly'], rate)

    cities.sort(key=lambda x: x['metrics']['score'], reverse=True)
    for i, c in enumerate(cities, 1):
        c['rank'] = i

    # Only call the site fully refreshed if most metros received real NOAA normals.
    starter = live < max(60, int(len(cities) * 0.80))

    obj['cities'] = cities
    obj['meta'] = {
        'generated': date.today().isoformat(),
        'live_noaa_cities': live,
        'live_eia_states': len(eia),
        'starter': starter,
        'normal_period': '1991–2020',
        'noaa_method': (
            'NCEI direct monthly-normal station files selected using the '
            'GHCN-Daily station inventory'
        ),
        'notes': (
            'NOAA 1991–2020 climate normals + latest EIA residential electricity rates. '
            'Recent NOAA observations are optional context. A city preserves its prior '
            'record only when no complete nearby official station file can be found.'
        )
    }

    json.dump(obj, open(DATA, 'w'), indent=2)
    print(f'NOAA live: {live}/{len(cities)}')

    # Fail visibly if the NOAA archive itself is unavailable rather than silently
    # deploying a mostly starter dataset.
    if live < 50:
        raise RuntimeError(
            f'Only {live}/{len(cities)} NOAA city normals updated. '
            'Refusing to deploy a mostly stale climate index.'
        )

    import subprocess, sys
    subprocess.check_call([sys.executable, str(ROOT / 'scripts/build_site.py')])


if __name__ == '__main__':
    main()
