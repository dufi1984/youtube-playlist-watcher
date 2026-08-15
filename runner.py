#!/usr/bin/env python3
import os
import sys
import json
import smtplib
import requests
import subprocess
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

CONFIG_FILE = 'playlists_config.json'
STATUS_FILE = 'latest_status.json'

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    env_playlists = os.environ.get('PLAYLIST_ID', '')
    p_ids = [p.strip() for p in env_playlists.split(',') if p.strip()]
    return {
        "pin_hash": "03ac674216f3e15c761ee1a5e255f067953623c8b388b4459e13f978d7c846f4",
        "playlists": [{"id": pid, "title": f"Playlist {pid[:6]}...", "emails": ["tamas.duffek@gmail.com"]} for pid in p_ids]
    }

def run_command(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    return result.returncode, result.stdout, result.stderr

def build_minimal_email(title, body_text):
    formatted_body = body_text.replace("\n", "<br>")
    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: Arial, sans-serif; color: #111827; line-height: 1.6; font-size: 15px; padding: 10px;">
        <div style="font-size: 16px; font-weight: bold; margin-bottom: 6px;">{title}</div>
        <div>{formatted_body}</div>
    </body>
    </html>
    """

def send_email_notification(to_emails, subject, text_content, playlist_title="YouTube playlist watcher", playlist_id=""):
    if not to_emails:
        to_emails = ["tamas.duffek@gmail.com"]

    if isinstance(to_emails, str):
        to_emails = [to_emails]

    html_content = build_minimal_email(playlist_title, text_content)

    resend_key = os.environ.get('RESEND_API_KEY', '').strip()
    from_email = os.environ.get('FROM_EMAIL', 'YouTube watcher <onboarding@resend.dev>').strip() or 'YouTube watcher <onboarding@resend.dev>'

    print(f"Resend Key configured: {'YES' if resend_key else 'NO'}")
    if resend_key:
        print(f"Sending email via Resend API to {to_emails} from '{from_email}'...")
        try:
            resp = requests.post(
                'https://api.resend.com/emails',
                headers={
                    'Authorization': f'Bearer {resend_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'from': from_email,
                    'to': to_emails,
                    'subject': 'YouTube playlist watcher',
                    'html': html_content,
                    'text': f"{playlist_title}\n\n{text_content}"
                },
                timeout=15
            )
            print(f"Resend HTTP Status: {resp.status_code}, Body: {resp.text}")
            if resp.status_code in [200, 201]:
                print(f"✅ Email successfully sent via Resend API to {to_emails}!")
                return True
            else:
                print(f"❌ Resend API error: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"❌ Exception calling Resend API: {e}")

    return False

def clean_report_text(report):
    lines = [line.strip() for line in report.split('\n') if line.strip()]
    cleaned = []
    for l in lines:
        if l.startswith('-> find another video') or l.startswith('https://www.youtube.com/results'):
            continue
        if l.startswith('[YPW] Changes detected'):
            continue
        cleaned.append(l)
    return '\n'.join(cleaned)

def main():
    api_key = os.environ.get('YOUTUBE_API_KEY', '').strip()
    force_test = os.environ.get('FORCE_TEST_ALERT', 'false').lower() in ['true', '1', 'yes']

    config = load_config()
    playlists = config.get('playlists', [])
    
    current_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    status_data = {
        "last_run": current_utc,
        "results": []
    }

    all_changes = []

    for item in playlists:
        pid = item['id']
        title = item.get('title', pid)
        target_emails = item.get('emails', [])
        if not target_emails:
            target_emails = ["tamas.duffek@gmail.com"]

        print(f"\n==========================================")
        print(f"Processing playlist: {title} ({pid})")
        print(f"==========================================")

        if api_key:
            # 1. Dump current playlist state
            code, out, err = run_command(f'python youtube_playlist_watcher.py --playlist-id "{pid}" dump --youtube-api-key "{api_key}"')
            if code != 0:
                print(f"Error dumping playlist {pid}: {err}")
            else:
                # 2. Compare with previous dump
                code, out, err = run_command(f'python youtube_playlist_watcher.py --playlist-id "{pid}" compare SECOND_TO_LAST LATEST')
                
                has_changes = bool(out and "Changes detected" in out)
                cleaned_out = clean_report_text(out) if has_changes else "No changes detected."
                
                playlist_result = {
                    "id": pid,
                    "title": title,
                    "has_changes": has_changes,
                    "report": cleaned_out
                }
                status_data["results"].append(playlist_result)

                if has_changes:
                    print(f"⚠️ Changes detected in playlist '{title}'!")
                    all_changes.append(f"{title}\n{cleaned_out}\n{'-'*40}")
                    send_email_notification(
                        target_emails,
                        'YouTube playlist watcher',
                        cleaned_out,
                        playlist_title=title,
                        playlist_id=pid
                    )

                # 3. Purge old dumps
                run_command(f'python youtube_playlist_watcher.py --playlist-id "{pid}" purge-dumps --keep-count 30')
        else:
            print("YOUTUBE_API_KEY not set, skipping dump/compare.")

    # Send clean test email if force_test is explicitly enabled
    if force_test:
        test_msg = "<b>Törölt videó</b>\nThe Cure - Burn 1994 HQ (The Crow)\nPozíció a listán: 42."
        print("\n--- Sending Minimalist Test Email ---")
        send_email_notification(
            ["tamas.duffek@gmail.com"],
            'YouTube playlist watcher',
            test_msg,
            playlist_title="Saját teszt lista",
            playlist_id=playlists[0]['id'] if playlists else "PLSfXEqbVqKrlZnCwypEnM7Aa4o15rknR8"
        )

    # Save latest status summary for Web UI
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(status_data, f, indent=2, ensure_ascii=False)

    if all_changes:
        with open('changes_report.txt', 'w', encoding='utf-8') as f:
            f.write("\n\n".join(all_changes))

if __name__ == '__main__':
    main()
