import msal  # type: ignore[import]
import requests
import os
import logging
import glob
import time
from typing import Any
from config import cfg

logging.basicConfig(filename='log.txt', level=logging.ERROR,
                    format='%(asctime)s - %(levelname)s - %(message)s')


def graph_request(method: str, url: str, headers: dict[str, str], **kwargs: Any) -> requests.Response:
    """
    Wrapper around requests that automatically handles Microsoft Graph
    429 ApplicationThrottled responses by honouring the Retry-After header
    and retrying up to cfg.GRAPH_MAX_RETRIES times.
    """
    for attempt in range(1, cfg.GRAPH_MAX_RETRIES + 1):
        if method == 'post':
            res = requests.post(url, headers=headers, **kwargs)
        elif method == 'put':
            res = requests.put(url, headers=headers, **kwargs)
        else:
            raise ValueError(f"Unsupported method: {method}")

        if res.status_code == 429:
            retry_after = int(res.headers.get('Retry-After', 30))
            print(f"\n  [!] Throttled (attempt {attempt}/{cfg.GRAPH_MAX_RETRIES}). "
                  f"Waiting {retry_after}s before retry...")
            time.sleep(retry_after + 1)
            continue

        return res

    raise RuntimeError(f"Request to {url} throttled after {cfg.GRAPH_MAX_RETRIES} retries.")


def send_mail():
    pdf_files = sorted(glob.glob("Summary_*.pdf"))

    if not pdf_files:
        print("[FAIL] No Summary PDFs found to send.")
        return

    print(f"[*] Found {len(pdf_files)} PDF(s) to send.")

    # Authenticate once for all emails
    try:
        authority = f"https://login.microsoftonline.com/{cfg.TENANT_ID}"
        app = msal.ConfidentialClientApplication(
            cfg.CLIENT_ID, authority=authority, client_credential=cfg.CLIENT_SECRET
        )
        token_res = app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        if 'access_token' not in token_res:
            raise RuntimeError(token_res.get('error_description', 'Unknown auth error'))
        token = token_res['access_token']
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        print("[OK] Authenticated with Microsoft Graph.\n")
    except Exception as e:
        logging.error(f"Failed to acquire token: {e}")
        print(f"[FAIL] Could not authenticate: {e}")
        return

    failed = []

    for i, file_path in enumerate(pdf_files, start=1):
        print(f"[{i}/{len(pdf_files)}] Processing: {file_path}")
        try:
            file_size = os.path.getsize(file_path)
            print(f"  [>] Size: {file_size / 1024 / 1024:.2f} MB")

            name_part = file_path.replace("Summary_", "").replace(".pdf", "").replace("_", " ")
            subject_line = f"{name_part} Camera Snapshot Summary"

            # 1. Create draft
            print("  [>] Creating draft...")
            draft_url = f"https://graph.microsoft.com/v1.0/users/{cfg.SENDER_EMAIL}/messages"
            draft_body = {
                "subject": subject_line,
                "body": {
                    "contentType": "HTML",
                    "content": f"<p>Attached is the {subject_line}.</p>"
                },
                "toRecipients": [{"emailAddress": {"address": cfg.RECIPIENT}}]
            }
            res = graph_request('post', draft_url, headers, json=draft_body)
            if res.status_code != 201:
                raise RuntimeError(f"Draft failed: {res.status_code} — {res.text}")
            message_id = res.json()['id']

            # 2. Create upload session
            print("  [>] Opening upload session...")
            session_url = (
                f"https://graph.microsoft.com/v1.0/users/{cfg.SENDER_EMAIL}"
                f"/messages/{message_id}/attachments/createUploadSession"
            )
            session_body = {
                "AttachmentItem": {
                    "attachmentType": "file",
                    "name": os.path.basename(file_path),
                    "size": file_size
                }
            }
            session_res = graph_request('post', session_url, headers, json=session_body)
            if session_res.status_code != 201:
                raise RuntimeError(f"Session failed: {session_res.status_code} — {session_res.text}")
            upload_url = session_res.json().get('uploadUrl')

            # 3. Upload in chunks (~3.2 MB per chunk)
            # Upload session URLs are pre-authed — do NOT send the auth header for chunks.
            chunk_size = 327680 * 10
            with open(file_path, 'rb') as f:
                start = 0
                while start < file_size:
                    data = f.read(chunk_size)
                    end = start + len(data)
                    range_header = f"bytes {start}-{end - 1}/{file_size}"
                    chunk_headers = {
                        'Content-Length': str(len(data)),
                        'Content-Range': range_header
                    }
                    for attempt in range(1, cfg.GRAPH_MAX_RETRIES + 1):
                        chunk_res = requests.put(upload_url, headers=chunk_headers, data=data)
                        if chunk_res.status_code == 429:
                            retry_after = int(chunk_res.headers.get('Retry-After', 30))
                            print(f"\n  [!] Chunk throttled. Waiting {retry_after}s...")
                            time.sleep(retry_after + 1)
                            continue
                        break
                    if chunk_res.status_code not in (200, 201, 202):
                        raise RuntimeError(
                            f"Chunk upload failed: {chunk_res.status_code} — {chunk_res.text}"
                        )
                    print(
                        f"      Uploaded {end / 1024 / 1024:.1f} MB"
                        f" / {file_size / 1024 / 1024:.1f} MB   ",
                        end='\r'
                    )
                    start = end

            # 4. Send
            print(f"\n  [>] Sending...")
            send_url = (
                f"https://graph.microsoft.com/v1.0/users/{cfg.SENDER_EMAIL}"
                f"/messages/{message_id}/send"
            )
            send_res = graph_request('post', send_url, headers)
            if send_res.status_code == 202:
                print(f"  [OK] Sent: {file_path}")
            else:
                raise RuntimeError(f"Send failed: {send_res.status_code} — {send_res.text}")

        except Exception as e:
            logging.error(f"Email failed for {file_path}: {e}")
            print(f"  [FAIL] {file_path} — {e}")
            failed.append(file_path)

        if i < len(pdf_files):
            print(f"  [~] Waiting {cfg.SEND_DELAY}s before next email...")
            time.sleep(cfg.SEND_DELAY)

    print("\n--- Done ---")
    print(f"  Sent:   {len(pdf_files) - len(failed)}/{len(pdf_files)}")
    if failed:
        print(f"  Failed: {len(failed)}")
        for f in failed:
            print(f"    - {f}")


if __name__ == "__main__":
    send_mail()
