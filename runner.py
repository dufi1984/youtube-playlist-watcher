#!/usr/bin/env python3
import os
import sys
import json
import smtplib
import subprocess
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

CONFIG_FILE = 'playlists_config.json'
STATUS_FILE = 'latest_status.json'

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    # Fallback to environment variables
    env_playlists = os.environ.get('PLAYLIST_ID', '')
    p_ids = [p.strip() for p in env_playlists.split(',') if p.strip()]
    return {
        "pin_hash": "03ac674216f3e15c761ee1a5e255f067953623c8b388b4459e13f978d7c846f4",
        "playlists": [{"id": pid, "title": f"Playlist {pid[:6]}...", "emails": []} for pid in p_ids]
    }

def run_command(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    return result.returncode, result.stdout, result.stderr

def send_email_notification(to_emails, subject, text_content):
    smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.environ.get('SMTP_PORT', '587'))
    smtp_user = os.environ.get('SMTP_USER', '')
    smtp_password = os.environ.get('SMTP_PASSWORD', '')

    if not smtp_user or not smtp_password:
        print(f"SMTP credentials not configured in GitHub Secrets. Skipping direct SMTP email to {to_emails}.")
        return False

    try:
        msg = MIMEMultipart()
        msg['From'] = smtp_user
        msg['To'] = ", ".join(to_emails)
        msg['Subject'] = subject

        msg.attach(MIMEText(text_content, 'plain', 'utf-8'))

        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.send_message(msg)
        server.quit()
        print(f"Direct SMTP email notification successfully sent to {to_emails}")
        return True
    except Exception as e:
        print(f"Error sending SMTP email: {e}")
        return False

def main():
    api_key = os.environ.get('YOUTUBE_API_KEY', '')
    force_test = os.environ.get('FORCE_TEST_ALERT', 'false').lower() == 'true'

    if not api_key:
        print("ERROR: YOUTUBE_API_KEY environment variable is missing.")
        sys.exit(1)

    config = load_config()
    playlists = config.get('playlists', [])
    
    status_data = {
        "last_run": subprocess.check_output(['date', '-u', '+%Y-%m-%d %H:%M:%S UTC']).decode().strip() if os.name != 'nt' else "Recently",
        "results": []
    }

    all_changes = []

    for item in playlists:
        pid = item['id']
        title = item.get('title', pid)
        target_emails = item.get('emails', [])

        print(f"\n==========================================")
        print(f"Processing playlist: {title} ({pid})")
        print(f"==========================================")

        # 1. Dump current playlist state
        print("--- Downloading current data ---")
        code, out, err = run_command(f'python youtube_playlist_watcher.py --playlist-id "{pid}" dump --youtube-api-key "{api_key}"')
        if code != 0:
            print(f"Error dumping playlist {pid}: {err}")
            continue

        # 2. Compare with previous dump
        print("--- Comparing with previous dump ---")
        code, out, err = run_command(f'python youtube_playlist_watcher.py --playlist-id "{pid}" compare SECOND_TO_LAST LATEST')
        
        has_changes = bool(out and "Changes detected" in out)
        
        playlist_result = {
            "id": pid,
            "title": title,
            "has_changes": has_changes,
            "report": out if has_changes else "No changes detected."
        }
        status_data["results"].append(playlist_result)

        if has_changes:
            print(f"⚠️ Changes detected in playlist '{title}'!")
            all_changes.append(f"Playlist: {title} ({pid})\n{out}\n{'-'*40}")
            
            if target_emails:
                send_email_notification(
                    target_emails,
                    f"⚠️ YouTube Playlist Változás: {title}",
                    out
                )

        # 3. Purge old dumps
        run_command(f'python youtube_playlist_watcher.py --playlist-id "{pid}" purge-dumps --keep-count 30')

    # If force test mode is requested
    if force_test:
        test_msg = "🧪 TESZT ÉRTESÍTÉS\n\nEz egy teszt üzenet a YouTube Playlist Watcher rendszertől.\nAz automatikus ellenőrzés és értesítési rendszer 100%-ban működik!"
        print("\n--- Generating GUARANTEED Test Alert ---")
        all_changes.append(test_msg)
        for item in playlists:
            target_emails = item.get('emails', [])
            if target_emails:
                send_email_notification(target_emails, "🧪 TESZT - YouTube Playlist Watcher", test_msg)

    # Save latest status summary for Web UI
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(status_data, f, indent=2, ensure_ascii=False)

    # Save combined changes report for GitHub Issue notification
    if all_changes:
        with open('changes_report.txt', 'w', encoding='utf-8') as f:
            f.write("\n\n".join(all_changes))

if __name__ == '__main__':
    main()
