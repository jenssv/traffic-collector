"""
Cek setiap titik di points.csv SEKALI sebelum collector dijalankan.

Untuk tiap titik, script menampilkan:
- kecepatan saat ini / free-flow dan kelas jalan (frc)
- link Google Maps ke awal dan akhir segmen jalan yang dipilih TomTom

Buka link-nya: kalau garisnya ada di jalan yang dimaksud, titik itu benar.
Kalau nyasar (jalan samping, tol, jalur lambat), geser koordinatnya di points.csv.

Pemakaian:
  set TOMTOM_API_KEY=key_kalian     (Windows)
  export TOMTOM_API_KEY=key_kalian  (Mac/Linux)
  python check_points.py
"""

import csv
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"


def maps_link(lat, lon):
    return f"https://www.google.com/maps?q={lat},{lon}"


def main():
    # strip() + buang tanda kutip: "set KEY="abc"" di CMD ikut menyimpan tanda kutipnya
    key = os.environ.get("TOMTOM_API_KEY", "").strip().strip('"').strip("'")
    if not key:
        raise SystemExit("TOMTOM_API_KEY belum di-set.")
    print(f"Key terbaca: {key[:4]}...{key[-4:]} ({len(key)} karakter)\n")

    with open(os.path.join(HERE, "points.csv"), newline="", encoding="utf-8-sig") as f:
        points = [r for r in csv.DictReader(f) if r.get("point_id") and not r["point_id"].startswith("#")]

    for p in points:
        try:
            lat = float(p["lat"].strip().replace(",", "."))
            lon = float(p["lon"].strip().replace(",", "."))
        except (ValueError, AttributeError):
            print(f"{p['point_id']} {p['name']}: koordinat tidak valid -> lat={p['lat']!r} lon={p['lon']!r}\n")
            continue

        params = {"point": f"{lat},{lon}", "unit": "KMPH", "key": key}
        try:
            with urllib.request.urlopen(URL + "?" + urllib.parse.urlencode(params), timeout=15) as r:
                d = json.loads(r.read())["flowSegmentData"]
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            print(f"{p['point_id']} {p['name']}: GAGAL HTTP {exc.code}")
            print(f"   koordinat dikirim: {lat},{lon}")
            print(f"   pesan TomTom: {body}\n")
            continue
        except Exception as exc:
            print(f"{p['point_id']} {p['name']}: GAGAL ({exc})\n")
            continue
        finally:
            time.sleep(0.5)  # jeda kecil agar tidak kena batas request per detik

        coords = d["coordinates"]["coordinate"]
        start, end = coords[0], coords[-1]
        ratio = d["currentSpeed"] / d["freeFlowSpeed"] if d["freeFlowSpeed"] else 0
        print(f"{p['point_id']} {p['name']}")
        print(f"   speed {d['currentSpeed']}/{d['freeFlowSpeed']} km/h (rasio {ratio:.2f}), "
              f"{d['frc']}, confidence {d['confidence']:.2f}")
        print(f"   titik input : {maps_link(lat, lon)}")
        print(f"   awal segmen : {maps_link(start['latitude'], start['longitude'])}")
        print(f"   akhir segmen: {maps_link(end['latitude'], end['longitude'])}\n")


if __name__ == "__main__":
    main()
