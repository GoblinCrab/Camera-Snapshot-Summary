import os
import sys
import subprocess
import logging
import urllib.parse
import time
import csv
import json
from config import cfg

# Force UTF-8 output on Windows so unicode characters in print() don't crash
# with UnicodeEncodeError on the default cp1252 console encoding.
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

logging.basicConfig(filename='log.txt', level=logging.ERROR,
                    format='%(asctime)s - %(levelname)s - %(message)s')

BRAND_TEMPLATES = {
    "provision": {
        "sub": "rtsp://{user}:{password}@{ip}:554/chID={ch}&streamType=sub",
        "main": "rtsp://{user}:{password}@{ip}:554/chID={ch}&streamType=main"
    },
    "provision_alt": {
        "sub": "rtsp://{user}:{password}@{ip}:554/Streaming/Channels/{ch}02",
        "main": "rtsp://{user}:{password}@{ip}:554/Streaming/Channels/{ch}01"
    },
    # NVR12 / newer Ossia OS firmware — uses query-string format, not path
    "provision_nvr12": {
        "sub":  "rtsp://{user}:{password}@{ip}:554?chID={ch}&streamType=sub",
        "main": "rtsp://{user}:{password}@{ip}:554?chID={ch}&streamType=main"
    },
    # Older Provision with explicit linkType=tcp
    "provision_tcp": {
        "sub":  "rtsp://{user}:{password}@{ip}:554/chID={ch}&streamType=sub&linkType=tcp",
        "main": "rtsp://{user}:{password}@{ip}:554/chID={ch}&streamType=main&linkType=tcp"
    },
    "hikvision": {
        "sub": "rtsp://{user}:{password}@{ip}:554/Streaming/Channels/{ch}02",
        "main": "rtsp://{user}:{password}@{ip}:554/Streaming/Channels/{ch}01"
    },
    "dahua": {
        "sub": "rtsp://{user}:{password}@{ip}:554/cam/realmonitor?channel={ch}&subtype=1",
        "main": "rtsp://{user}:{password}@{ip}:554/cam/realmonitor?channel={ch}&subtype=0"
    },
    "dahua_alt": {
        "sub": "rtsp://{user}:{password}@{ip}:554/h264/ch{ch}/sub/av_stream",
        "main": "rtsp://{user}:{password}@{ip}:554/h264/ch{ch}/main/av_stream"
    },
    # Milesight channels are 0-indexed in the URL:
    #   ch=0 -> ch_100 (main) / ch_400 (sub) = physical channel 1
    #   ch=1 -> ch_101 (main) / ch_401 (sub) = physical channel 2  ...etc.
    "milesight": {
        "sub": "rtsp://{user}:{password}@{ip}:554/ch_4{ch:02d}",
        "main": "rtsp://{user}:{password}@{ip}:554/ch_1{ch:02d}"
    },
    "tp-link": {
        "sub": "rtsp://{user}:{password}@{ip}:554/ch{ch}/sub/av_stream",
        "main": "rtsp://{user}:{password}@{ip}:554/ch{ch}/main/av_stream"
    },
}

# Brands whose URL channel numbers start at 0 instead of 1.
CHANNEL_START = {
    "milesight": 0,
}

# Statuses that mean "a stream exists here, even if slow"
ALIVE = {"OK", "TIMEOUT"}


def format_ip_filename(ip, ch):
    clean_ip = ip.replace(".", "_")
    return f"{clean_ip}-CH_{ch:02d}.jpg"


def probe_stream(url, timeout_s=10, max_retries=3):
    for attempt in range(max_retries):
        cmd = [
            'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
            '-rtsp_transport', 'tcp', '-timeout', '5000000',
            '-i', url, '-frames:v', '1', '-f', 'null', '-'
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
            if result.returncode == 0: return "OK"
            if "401" in result.stderr or "Unauthorized" in result.stderr: return "401"
            if "404" in result.stderr and attempt == max_retries - 1: return "404"
        except subprocess.TimeoutExpired:
            if attempt == max_retries - 1: return "TIMEOUT"
        except Exception: pass
        time.sleep(0.5)
    return "ERROR"


def is_host_reachable(ip):
    param = '-n' if os.name == 'nt' else '-c'
    command = ['ping', param, '1', '-w', '1000', ip]
    return subprocess.run(command, capture_output=True).returncode == 0


def smart_limit_discovery(ip, detected_brand, safe_user, safe_pass):
    """
    Fast two-phase channel limit discovery. Returns channel COUNT (not highest ch number).

    Phase 1 — Step down through anchors (64, 48, 32, 16, 8) to find the highest
               anchor that responds (OK or TIMEOUT), establishing a floor.
    Phase 2 — Scan upward from floor+1. Requires 3 consecutive dead channels before
               declaring the ceiling — avoids a false stop on a single offline camera.
    """
    template = BRAND_TEMPLATES[detected_brand]["sub"]
    ch_start = CHANNEL_START.get(detected_brand, 1)
    anchors = [64, 48, 32, 16, 8]

    print(f"    [>] Phase 1: Stepping through anchors to find floor...", flush=True)
    floor = None
    for anchor in anchors:
        url = template.format(user=safe_user, password=safe_pass, ip=ip, ch=anchor)
        status = probe_stream(url, timeout_s=8, max_retries=1)
        print(f"        CH {anchor:02d}: {status}")
        if status in ALIVE:
            floor = anchor
            break

    if floor is None:
        print(f"    [!] No anchor responded — sequential scan from CH {ch_start:02d}")
        scan_start = ch_start
        max_ch = ch_start - 1
    else:
        print(f"    [>] Floor found at CH {floor:02d}. Phase 2: Scanning upward...")
        scan_start = floor + 1
        max_ch = floor

    consecutive_dead = 0
    for ch in range(scan_start, scan_start + 36):
        url = template.format(user=safe_user, password=safe_pass, ip=ip, ch=ch)
        status = probe_stream(url, timeout_s=8, max_retries=1)
        print(f"        CH {ch:02d}: {status}")
        if status in ALIVE:
            max_ch = ch
            consecutive_dead = 0
        else:
            consecutive_dead += 1
            if consecutive_dead >= 3:
                print(f"    [>] Ceiling confirmed (3 consecutive dead) — stopping at CH {max_ch:02d}.")
                break

    if max_ch < ch_start:
        return 0
    return max_ch - ch_start + 1


def discover_nvr(ip, expected_limit, name, existing_brand=None, excluded_str=""):
    if not is_host_reachable(ip):
        print(f"[!] {ip} ({name}) is not reachable — skipping.")
        return [], 0, "unreachable"

    all_targets = []
    final_count = 0
    detected_brand = "unknown"

    if existing_brand and existing_brand.lower() in BRAND_TEMPLATES:
        brands_to_test = [existing_brand.lower()] + [
            b for b in BRAND_TEMPLATES.keys() if b != existing_brand.lower()
        ]
        print(f"[*] Discovering {ip} ({name})  [cached brand: {existing_brand}]...")
    else:
        brands_to_test = list(BRAND_TEMPLATES.keys())
        print(f"[*] Discovering {ip} ({name})...")

    excluded_list = [int(x.strip()) for x in excluded_str.split(';') if x.strip().isdigit()]

    for brand in brands_to_test:
        current_pass = cfg.NVR_TPLINK_PASS if "tp-link" in brand.lower() else cfg.NVR_PASS
        safe_user = urllib.parse.quote(cfg.NVR_USER)
        safe_pass = urllib.parse.quote(current_pass)

        ch_start = CHANNEL_START.get(brand, 1)
        template = BRAND_TEMPLATES[brand]['sub']

        print(f"    [?] Trying brand: {brand.upper()}...", end=" ", flush=True)

        confirmed = False
        confirmed_ch = ch_start
        for test_ch in (ch_start, ch_start + 1):
            test_url = template.format(user=safe_user, password=safe_pass, ip=ip, ch=test_ch)
            result = probe_stream(test_url, timeout_s=10, max_retries=2)
            if result in ALIVE:
                confirmed = True
                confirmed_ch = test_ch
                break

        if not confirmed:
            print(f"No ({result})")
            continue

        confirmed_human_ch = confirmed_ch - ch_start + 1
        detected_brand = brand
        print(f"Confirmed! (CH {confirmed_human_ch} -> {result})")
        print(f"    [+] Detected Brand: {brand.upper()}")

        if expected_limit == "?":
            print(f"    [?] Channel count unknown - running smart limit discovery...")
            final_count = smart_limit_discovery(ip, brand, safe_user, safe_pass)
            print(f"    [+] Discovered {final_count} channel(s).")
        else:
            final_count = int(expected_limit)
            print(f"    [>] Using expected channel count: {final_count}")

        skipped = []
        for idx in range(final_count):
            ch = ch_start + idx
            original_ch = idx + 1
            if original_ch in excluded_list:
                skipped.append(original_ch)
                continue
            main_url = BRAND_TEMPLATES[brand]['main'].format(
                user=safe_user, password=safe_pass, ip=ip, ch=ch
            )
            all_targets.append({
                'ip': ip, 'brand': brand, 'ch': ch, 'url': main_url,
                'name': name, 'original_ch': original_ch
            })

        queued = final_count - len(skipped)
        print(f"    [>] Queued {queued} channel(s) for capture", end="")
        if skipped:
            print(f" | Excluded: CH {', '.join(str(c) for c in skipped)}", end="")
        print()
        break

    if detected_brand == "unknown":
        print(f"    [!] Failed to identify brand for {ip} - no brands responded OK or TIMEOUT")

    return all_targets, final_count, detected_brand


if __name__ == "__main__":
    all_targets, csv_rows = [], []

    try:
        with open('targets.csv', mode='r') as infile:
            reader = csv.DictReader(infile)
            fieldnames = list(reader.fieldnames)
            if "Brand" not in fieldnames:
                fieldnames.append("Brand")
            for row in reader:
                clean_row = {k.strip(): (v.strip() if v is not None else "") for k, v in row.items()}
                csv_rows.append(clean_row)
    except FileNotFoundError:
        print("[!] targets.csv missing.")
        exit()

    for row in csv_rows:
        targets, updated_count, brand = discover_nvr(
            row['IP'],
            row.get('Expected_Channels', '?'),
            row['Establishment'],
            row.get('Brand'),
            row.get('Excluded_Channels', '')
        )
        all_targets.extend(targets)
        row['Expected_Channels'] = str(updated_count)
        row['Brand'] = brand

    fieldnames = ["IP", "Expected_Channels", "Establishment", "Brand", "Excluded_Channels"]
    while True:
        try:
            with open('targets.csv', mode='w', newline='') as outfile:
                writer = csv.DictWriter(outfile, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(csv_rows)
            print("--- [targets.csv updated with brands and limits] ---\n")
            break
        except PermissionError:
            input("[!] Cannot write to targets.csv - close the file then press Enter to retry...")

    manifest_data = {}
    for t in all_targets:
        est_name = t['name']
        filename = format_ip_filename(t['ip'], t['original_ch'])
        if est_name not in manifest_data:
            manifest_data[est_name] = []
        manifest_data[est_name].append(filename)

    with open(cfg.MANIFEST_FILE, "w") as f:
        json.dump(manifest_data, f, indent=4)
    print(f"--- [manifest.json written: {len(manifest_data)} establishment(s)] ---\n")

    with open(cfg.CAPTURE_QUEUE_FILE, "w") as f:
        json.dump(all_targets, f, indent=4)
    print(f"--- [capture_queue.json written: {len(all_targets)} channel(s) queued] ---")
    print(f"--- Run capture.py to take snapshots ---")