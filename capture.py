import os
import subprocess
import logging
import urllib.parse
import time
import json
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont
from config import cfg

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
    "provision_nvr12": {
        "sub":  "rtsp://{user}:{password}@{ip}:554?chID={ch}&streamType=sub",
        "main": "rtsp://{user}:{password}@{ip}:554?chID={ch}&streamType=main"
    },
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


def create_placeholder(filename, ip, original_ch):
    img = Image.new('RGB', (704, 576), color=(0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 32)
    except:
        font = ImageFont.load_default()
    text = f"Offline: {ip} Ch {original_ch}"
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(
        ((704 - (bbox[2] - bbox[0])) / 2, (576 - (bbox[3] - bbox[1])) / 2),
        text, fill=(255, 255, 255), font=font
    )
    img.save(f"{cfg.SNAPSHOTS_DIR}/{filename}", "JPEG")


def capture_target(target):
    ip = target['ip']
    brand = target['brand']
    orig_ch = target['original_ch']
    url_ch = target['ch']
    filename = format_ip_filename(ip, orig_ch)
    output = f"{cfg.SNAPSHOTS_DIR}/{filename}"

    current_pass = cfg.NVR_TPLINK_PASS if "tp-link" in brand.lower() else cfg.NVR_PASS
    safe_user = urllib.parse.quote(cfg.NVR_USER)
    safe_pass = urllib.parse.quote(current_pass)

    url = BRAND_TEMPLATES[brand]["main"].format(
        user=safe_user, password=safe_pass, ip=ip, ch=url_ch
    )
    cmd = [
        'ffmpeg', '-y', '-hide_banner', '-loglevel', 'error',
        '-rtsp_transport', 'tcp', '-timeout', '8000000',
        '-i', url, '-frames:v', '1', '-q:v', '4', output
    ]
    for _ in range(cfg.CAPTURE_RETRIES):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=cfg.CAPTURE_TIMEOUT_S)
            if result.returncode == 0 and os.path.exists(output) and os.path.getsize(output) > 0:
                print(f"  [OK]   {filename}")
                return
        except Exception:
            pass
        time.sleep(1)

    create_placeholder(filename, ip, orig_ch)
    print(f"  [FAIL] {filename}")


if __name__ == "__main__":
    try:
        with open(cfg.CAPTURE_QUEUE_FILE, 'r') as f:
            all_targets = json.load(f)
    except FileNotFoundError:
        print(f"[!] {cfg.CAPTURE_QUEUE_FILE} not found - run discover.py first.")
        exit()

    if not all_targets:
        print("[!] Capture queue is empty - nothing to do.")
        exit()

    if not os.path.exists(cfg.SNAPSHOTS_DIR):
        os.makedirs(cfg.SNAPSHOTS_DIR)
    else:
        for f in os.listdir(cfg.SNAPSHOTS_DIR):
            os.remove(os.path.join(cfg.SNAPSHOTS_DIR, f))
        print(f"[*] Cleared existing snapshots.")

    print(f"--- Starting Capture: {len(all_targets)} channel(s) across {cfg.CAPTURE_MAX_WORKERS} workers ---\n")
    with ThreadPoolExecutor(max_workers=cfg.CAPTURE_MAX_WORKERS) as ex:
        ex.map(capture_target, all_targets)
    print("\n--- Done ---")
