"""
Traffic collector — TomTom Traffic Flow + Open-Meteo.

Satu kali jalan = ambil data semua titik di points.csv, lalu simpan.
- Di GitHub Actions / PC  : ditambahkan ke data/traffic.csv
- Di AWS Lambda           : disimpan sebagai file CSV baru di S3 (1 file per run)

Hanya memakai library bawaan Python (tanpa pip install), kecuali boto3
yang sudah tersedia otomatis di AWS Lambda.

Environment variable:
  TOMTOM_API_KEY  (wajib)   API key TomTom
  S3_BUCKET       (Lambda)  nama bucket S3 tujuan
"""

import csv
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7))
HERE = os.path.dirname(os.path.abspath(__file__))
POINTS_FILE = os.path.join(HERE, "points.csv")
OUTPUT_FILE = os.path.join(HERE, "data", "traffic.csv")

# Key evaluasi TomTom membatasi jumlah request per detik. Request yang terlalu
# rapat ditolak (terlihat sebagai HTTP 400), jadi beri jeda dan coba ulang.
DELAY_BETWEEN_POINTS = 1.0  # detik
RETRIES = 2
RETRY_WAIT = 3.0  # detik

TOMTOM_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

# Titik pusat Jakarta untuk data cuaca (cuaca bisa di-backfill per titik nanti
# lewat Open-Meteo Historical API kalau dibutuhkan).
WEATHER_LAT, WEATHER_LON = -6.2, 106.8

COLUMNS = [
    "timestamp_wib",
    "point_id",
    "name",
    "lat",
    "lon",
    "current_speed",
    "free_flow_speed",
    "current_travel_time",
    "free_flow_travel_time",
    "confidence",
    "road_closure",
    "frc",
    "precipitation_mm",
    "temperature_c",
    "error",
]


def get_json(url, params, timeout=15):
    full_url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full_url, headers={"User-Agent": "traffic-collector/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_points():
    with open(POINTS_FILE, newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r.get("point_id") and not r["point_id"].startswith("#")]
    if not rows:
        raise SystemExit("points.csv masih kosong. Isi dulu titik ruas jalan.")
    return rows


def fetch_weather():
    try:
        data = get_json(WEATHER_URL, {
            "latitude": WEATHER_LAT,
            "longitude": WEATHER_LON,
            "current": "precipitation,temperature_2m",
            "timezone": "Asia/Jakarta",
        })
        cur = data.get("current", {})
        return cur.get("precipitation"), cur.get("temperature_2m")
    except Exception as exc:  # cuaca gagal tidak boleh menggagalkan data lalu lintas
        print(f"[weather] gagal: {exc}")
        return None, None


def fetch_point(point, api_key):
    lat = float(str(point["lat"]).strip().replace(",", "."))
    lon = float(str(point["lon"]).strip().replace(",", "."))
    params = {"point": f"{lat},{lon}", "unit": "KMPH", "key": api_key}
    for attempt in range(RETRIES + 1):
        try:
            data = get_json(TOMTOM_URL, params)["flowSegmentData"]
            break
        except urllib.error.HTTPError as exc:
            # 400/403/429 bisa muncul karena batas request per detik: tunggu, coba lagi
            if exc.code in (400, 403, 429) and attempt < RETRIES:
                time.sleep(RETRY_WAIT * (attempt + 1))
                continue
            raise
    return {
        "current_speed": data.get("currentSpeed"),
        "free_flow_speed": data.get("freeFlowSpeed"),
        "current_travel_time": data.get("currentTravelTime"),
        "free_flow_travel_time": data.get("freeFlowTravelTime"),
        "confidence": data.get("confidence"),
        "road_closure": data.get("roadClosure"),
        "frc": data.get("frc"),
    }


def collect():
    api_key = os.environ.get("TOMTOM_API_KEY", "").strip().strip('"').strip("'")
    if not api_key:
        raise SystemExit("TOMTOM_API_KEY belum di-set.")

    timestamp = datetime.now(WIB).strftime("%Y-%m-%d %H:%M:%S")
    precipitation, temperature = fetch_weather()
    rows = []

    for p in load_points():
        row = {c: "" for c in COLUMNS}
        row.update({
            "timestamp_wib": timestamp,
            "point_id": p["point_id"],
            "name": p.get("name", ""),
            "lat": p["lat"],
            "lon": p["lon"],
            "precipitation_mm": precipitation,
            "temperature_c": temperature,
        })
        try:
            row.update(fetch_point(p, api_key))
        except urllib.error.HTTPError as exc:
            row["error"] = f"HTTP {exc.code}"
        except Exception as exc:
            row["error"] = type(exc).__name__
        rows.append(row)
        status = row["error"] or f"{row['current_speed']}/{row['free_flow_speed']} km/h"
        print(f"[{p['point_id']}] {status}")
        time.sleep(DELAY_BETWEEN_POINTS)

    return timestamp, rows


def to_csv_text(rows, header=True):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=COLUMNS)
    if header:
        writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


def save_local(rows):
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    new_file = not os.path.exists(OUTPUT_FILE)
    with open(OUTPUT_FILE, "a", newline="", encoding="utf-8") as f:
        f.write(to_csv_text(rows, header=new_file))
    print(f"Tersimpan {len(rows)} baris ke {OUTPUT_FILE}")


def save_s3(timestamp, rows):
    import boto3  # tersedia otomatis di AWS Lambda

    bucket = os.environ["S3_BUCKET"]
    day, clock = timestamp.split(" ")
    key = f"raw/{day}/{clock.replace(':', '')}.csv"
    boto3.client("s3").put_object(Bucket=bucket, Key=key, Body=to_csv_text(rows).encode("utf-8"))
    print(f"Tersimpan {len(rows)} baris ke s3://{bucket}/{key}")


def lambda_handler(event, context):
    """Entry point untuk AWS Lambda."""
    timestamp, rows = collect()
    save_s3(timestamp, rows)
    return {"rows": len(rows), "errors": sum(1 for r in rows if r["error"])}


if __name__ == "__main__":
    _, collected = collect()
    save_local(collected)
