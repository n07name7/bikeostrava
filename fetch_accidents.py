import io
import json
import logging
from datetime import date, datetime
from html.parser import HTMLParser

import rarfile
import requests
from pyproj import Transformer

# Czech Police data URL pattern
POLICE_URL = "https://policie.gov.cz/soubor/data-web-{mm:02d}-{yyyy}-rar.aspx"
HEADERS = {"User-Agent": "Mozilla/5.0 BikeOstrava/1.0"}

OSTRAVA_LAT_MIN, OSTRAVA_LAT_MAX = 49.70, 50.00
OSTRAVA_LNG_MIN, OSTRAVA_LNG_MAX = 17.90, 18.60

_TRANSFORMER = Transformer.from_crs("EPSG:5514", "EPSG:4326", always_xy=True)


def _in_ostrava(lat, lng):
    return (OSTRAVA_LAT_MIN <= lat <= OSTRAVA_LAT_MAX and
            OSTRAVA_LNG_MIN <= lng <= OSTRAVA_LNG_MAX)


def _parse_date(s):
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_html_table(html):
    class _P(HTMLParser):
        def __init__(self):
            super().__init__()
            self.rows = []
            self._cur = []
            self._in_cell = False
            self._text = ""

        def handle_starttag(self, tag, attrs):
            if tag in ("td", "th"):
                self._in_cell = True
                self._text = ""
            elif tag == "tr":
                self._cur = []

        def handle_endtag(self, tag):
            if tag in ("td", "th"):
                self._cur.append(self._text.strip())
                self._in_cell = False
            elif tag == "tr" and self._cur:
                self.rows.append(self._cur)
                self._cur = []

        def handle_data(self, data):
            if self._in_cell:
                self._text += data

    p = _P()
    p.feed(html)
    return p.rows


def load_month(yyyy, mm):
    url = POLICE_URL.format(mm=mm, yyyy=yyyy)
    print(f"Fetching {url}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=60)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except Exception as exc:
        print("Download error:", exc)
        return None

    rf = rarfile.RarFile(io.BytesIO(resp.content))
    gps_data = {}
    try:
        raw_gps = rf.read("IntGPS.xls").decode("cp1250", errors="replace")
        rows_gps = _parse_html_table(raw_gps)
        if rows_gps:
            header = rows_gps[0]
            idx = {h: i for i, h in enumerate(header)}
            for row in rows_gps[1:]:
                try:
                    p1 = row[idx["p1"]].strip()
                    d_val = float(row[idx["d"]].replace(",", "."))
                    e_val = float(row[idx["e"]].replace(",", "."))
                    lng, lat = _TRANSFORMER.transform(e_val, d_val)
                    if _in_ostrava(lat, lng):
                        gps_data[p1] = (lat, lng)
                except Exception:
                    pass
    except Exception:
        pass

    if not gps_data:
        return {}

    try:
        raw_c = rf.read("Ichodci.xls").decode("cp1250", errors="replace")
        rows_c = _parse_html_table(raw_c)
        if rows_c:
            hc = rows_c[0]
            ic = {h: i for i, h in enumerate(hc)}
            bike_p1 = {
                row[ic["p1"]].strip()
                for row in rows_c[1:]
                if len(row) > ic.get("p29", 999) and row[ic["p29"]].strip() == "2"
            }
            gps_data = {p1: coords for p1, coords in gps_data.items() if p1 in bike_p1}
    except Exception:
        pass

    meta_data = {}
    try:
        raw_n = rf.read("Inehody.xls").decode("cp1250", errors="replace")
        rows_n = _parse_html_table(raw_n)
        if rows_n:
            header = rows_n[0]
            idx = {h: i for i, h in enumerate(header)}
            for row in rows_n[1:]:
                try:
                    p1 = row[idx["p1"]].strip()
                    if p1 not in gps_data:
                        continue
                    date_str = row[idx.get("p2a", -1)].strip() if "p2a" in idx else ""
                    d_obj = _parse_date(date_str)
                    def _int(col):
                        try: return int(row[idx[col]].strip() or "0")
                        except: return 0
                    if "p13a" in idx and _int("p13a") > 0: severity = "smrtelna"
                    elif "p13b" in idx and _int("p13b") > 0: severity = "tezka"
                    elif "p13c" in idx and _int("p13c") > 0: severity = "lehka"
                    else: severity = "s hmotnou skodou"
                    meta_data[p1] = (d_obj, severity)
                except Exception:
                    pass
    except Exception:
        pass

    result = {}
    for p1, (lat, lng) in gps_data.items():
        acc_date, severity = meta_data.get(p1, (None, ""))
        result[p1] = {"lat": lat, "lng": lng, "date": acc_date, "severity": severity}
    return result

def main():
    today = date.today()
    y, m = today.year, today.month
    months_to_try = []
    for _ in range(6):
        months_to_try.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1

    all_records = {}
    for yyyy, mm in months_to_try:
        records = load_month(yyyy, mm)
        if records:
            for p1, rec in records.items():
                all_records[p1] = rec
                
    with open("routing/management/commands/accidents.json", "w") as f:
        json.dump(list(all_records.values()), f, indent=2)
    print(f"Saved {len(all_records)} accidents to accidents.json")

if __name__ == "__main__":
    main()
