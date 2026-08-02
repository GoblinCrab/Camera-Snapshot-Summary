import os
import subprocess
import logging
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont
import json
import urllib.parse
import time
import csv

# Setup logging
logging.basicConfig(filename='log.txt', level=logging.ERROR, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

# --- CONFIGURATION ---
USER = "monitoring"
PASS = "Subway2026"
TP_LINK_PASS = "Subway2026!" # Special password for TP-Link

BRAND_TEMPLATES = {
    "provision": {
        "sub": "rtsp://{user}:{password}@{ip}:554/chID={ch}&streamType=sub",
        "main": "rtsp://{user}:{password}@{ip}:554/chID={ch}&streamType=main"
    },
    "hikvision": {
        "sub": "rtsp://{user}:{password}@{ip}:554/Streaming/Channels/{ch}02",
        "main": "rtsp://{user}:{password}@{ip}:554/Streaming/Channels/{ch}01"
    },
    "dahua": {
        "sub": "rtsp://{user}:{password}@{ip}:554/cam/realmonitor?channel={ch}&subtype=1",
        "main": "rtsp://{user}:{password}@{ip}:554/cam/realmonitor?channel={ch}&subtype=0"
    },
    "milesight": {
        "sub": "rtsp://{user}:{password}@{ip}:554/ch_4{ch:02d}",
        "main": "rtsp://{user}:{password}@{ip}:554/ch_1{ch:02d}"
    },
    "tp-link": {
        "sub": "rtsp://{user}:{password}@{ip}:554/ch{ch}/sub/av_stream",
        "main": "rtsp://{user}:{password}@{ip}:554/ch{ch}/main/av_stream"
    }
}

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
    Fast two-phase channel limit discovery:
      Phase 1 - Step down through anchors (64, 48, 32, 24, 16, 8) to find the highest
                anchor that responds, establishing a floor.
      Phase 2 - Scan upward from that floor+1 until a channel stops responding,
                giving the exact channel count.
    """
    template = BRAND_TEMPLATES[detected_brand]["sub"]
    anchors = [64, 48, 32, 24, 16, 8]

    print(f"    [>] Phase 1: Stepping through anchors to find floor...", flush=True)
    floor = 0
    for anchor in anchors:
        url = template.format(user=safe_user, password=safe_pass, ip=ip, ch=anchor)
        status = probe_stream(url, timeout_s=5, max_retries=1)
        print(f"        CH {anchor:02d}: {status}")
        if status == "OK":
            floor = anchor
            break

    if floor == 0:
        print(f"    [!] No anchor responded — defaulting to sequential scan from CH 01")
        floor_start = 1
    else:
        print(f"    [>] Floor found at CH {floor}. Phase 2: Scanning upward...")
        floor_start = floor + 1

    # Phase 2: scan upward from floor+1 to find exact ceiling
    limit = floor
    for ch in range(floor_start, floor + 33):  # cap scan window at +32 above floor
        url = template.format(user=safe_user, password=safe_pass, ip=ip, ch=ch)
        status = probe_stream(url, timeout_s=5, max_retries=1)
        print(f"        CH {ch:02d}: {status}")
        if status in ["OK", "TIMEOUT"]:
            limit = ch
        else:
            print(f"    [>] Ceiling found — stopping at CH {limit}.")
            break

    return limit

def discover_nvr(ip, expected_limit, name, existing_brand=None, excluded_str=""):
    """
    Probes an IP address to identify the NVR brand and the number of active channels.
    Supports alternate passwords for TP-Link VIGI hardware.
    """
    all_targets = []
    final_limit = 0
    detected_brand = "unknown"

    # Determine which brands to test
    if existing_brand and existing_brand.lower() in BRAND_TEMPLATES:
        brands_to_test = [existing_brand.lower()]
    else:
        brands_to_test = list(BRAND_TEMPLATES.keys())

    print(f"[*] Discovering {ip} ({name})...")

    # Parse the exclusion string into a list of integers
    excluded_list = [int(x.strip()) for x in excluded_str.split(';') if x.strip().isdigit()]

    for brand in brands_to_test:
        # --- PASSWORD LOGIC ---
        current_pass = TP_LINK_PASS if "tp-link" in brand.lower() else PASS
        safe_user = urllib.parse.quote(USER)
        safe_pass = urllib.parse.quote(current_pass)
        # ----------------------

        template = BRAND_TEMPLATES[brand]['sub']
        test_url = template.format(user=safe_user, password=safe_pass, ip=ip, ch=1)

        # Brand detection requires a definitive OK - TIMEOUT is not a confirmation
        print(f"    [?] Trying brand: {brand.upper()}...", end=" ", flush=True)
        result = probe_stream(test_url, timeout_s=5, max_retries=1)

        if result != "OK":
            print(f"No ({result})")
            continue

        detected_brand = brand
        print(f"Confirmed!")
        print(f"    [+] Detected Brand: {brand.upper()}")

        # If limit is unknown, use smart two-phase discovery
        if expected_limit == "?":
            print(f"    [?] Channel count unknown - running smart limit discovery...")
            final_limit = smart_limit_discovery(ip, brand, safe_user, safe_pass)
            print(f"    [+] Discovered {final_limit} channel(s).")
        else:
            final_limit = int(expected_limit)
            print(f"    [>] Using expected channel count: {final_limit}")

        # Build targets but SKIP excluded channels
        skipped = []
        for ch in range(1, final_limit + 1):
            if ch in excluded_list:
                skipped.append(ch)
                continue
            main_url = BRAND_TEMPLATES[brand]['main'].format(
                user=safe_user, password=safe_pass, ip=ip, ch=ch
            )
            all_targets.append({
                'ip': ip, 'brand': brand, 'ch': ch, 'url': main_url,
                'name': name, 'original_ch': ch
            })

        queued = final_limit - len(skipped)
        print(f"    [>] Queued {queued} channel(s) for capture", end="")
        if skipped:
            print(f" | Excluded: CH {', '.join(str(c) for c in skipped)}", end="")
        print()
        break

    if detected_brand == "unknown":
        print(f"    [!] Failed to identify brand for {ip} - no brands responded OK")

    return all_targets, final_limit, detected_brand

def create_placeholder(filename, ip, original_ch):
    img = Image.new('RGB', (704, 576), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)
    try: font = ImageFont.truetype("arial.ttf", 32)
    except: font = ImageFont.load_default()
    text = f"Offline: {ip} Ch {original_ch}"
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(((704-(bbox[2]-bbox[0]))/2, (576-(bbox[3]-bbox[1]))/2), text, fill=(255, 255, 255), font=font)
    img.save(f"snapshots/{filename}", "JPEG")

def capture_target(target):
    ip, brand, orig_ch = target['ip'], target['brand'], target['original_ch']
    filename = format_ip_filename(ip, orig_ch)
    output = f"snapshots/{filename}"
    current_pass = TP_LINK_PASS if "tp-link" in brand.lower() else PASS
    safe_user = urllib.parse.quote(USER)
    safe_pass = urllib.parse.quote(current_pass)
    
    url = BRAND_TEMPLATES[brand]["main"].format(
        user=safe_user, password=safe_pass, ip=ip, ch=orig_ch
    )
    cmd = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-rtsp_transport', 'tcp', 
           '-timeout', '8000000', '-i', url, '-frames:v', '1', '-q:v', '4', output]
    for _ in range(2):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode == 0 and os.path.exists(output) and os.path.getsize(output) > 0:
                print(f"  [OK] Snapshot saved: {filename}")
                return
        except Exception: pass
        time.sleep(1)
    create_placeholder(filename, ip, orig_ch)
    print(f"  [FAIL] Snapshot failed: {filename}")

if __name__ == "__main__":
    if not os.path.exists("snapshots"): os.makedirs("snapshots")
    for f in os.listdir("snapshots"): os.remove(os.path.join("snapshots", f))

    all_targets, csv_rows = [], []
    
    try:
        with open('targets.csv', mode='r') as infile:
            reader = csv.DictReader(infile)
            fieldnames = list(reader.fieldnames)
            if "Brand" not in fieldnames: fieldnames.append("Brand")
            for row in reader:
                clean_row = {k.strip(): (v.strip() if v is not None else "") for k, v in row.items()}
                csv_rows.append(clean_row)
    except FileNotFoundError:
        print("[!] targets.csv missing."); exit()

    for row in csv_rows:
        # Pass row.get('Excluded_Channels', '') to the function
        targets, updated_limit, brand = discover_nvr(
            row['IP'], 
            row.get('Expected_Channels', '?'), 
            row['Establishment'], 
            row.get('Brand'),
            row.get('Excluded_Channels', '')
        )
        all_targets.extend(targets)
        row['Expected_Channels'] = str(updated_limit)
        row['Brand'] = brand

    with open('targets.csv', mode='w', newline='') as outfile:
        fieldnames = ["IP", "Expected_Channels", "Establishment", "Brand", "Excluded_Channels"]
        writer = csv.DictWriter(outfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)
        print("--- [targets.csv updated with brands and limits] ---\n")

    # GROUP SNAPSHOTS BY ESTABLISHMENT
    manifest_data = {}
    for t in all_targets:
        est_name = t['name']
        filename = format_ip_filename(t['ip'], t['original_ch'])
        if est_name not in manifest_data:
            manifest_data[est_name] = []
        manifest_data[est_name].append(filename)

    with open("manifest.json", "w") as f: 
        json.dump(manifest_data, f, indent=4)

    print(f"--- Starting High-Resolution Capture: {len(all_targets)} Channels ---")
    if all_targets:
        with ThreadPoolExecutor(max_workers=5) as ex: ex.map(capture_target, all_targets)
    print("--- Done ---")