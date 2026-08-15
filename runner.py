#!/usr/bin/env python3
import os
import sys
import json
import smtplib
import requests
import subprocess
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
        <div style="font-size: 16px; font-weight: bold; margin-bottom: 8px;">{title}</div>
        <div>{formatted_body}</div>
    </body>
    </html>
    """

def send_email_notification(to_emails, subject, text_content, playlist_title="YouTube playlist watcher", playlist_id=""):
    if not to_emails:
        to_emails = ["tamas.duffek@gmail.com"]

    html_content = build_minimal_email(playlist_title, text_content)

    # 1. Option: Resend API
    resend_key = os.environ.get('RESEND_API_KEY', '')
    from_email = os.environ.get('FROM_EMAIL', 'YouTube Watcher <onboarding@resend.dev>')

    if resend_key:
        print(f"Attempting to send email via Resend API to {to_emails}...")
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
            print(f"Resend response status: {resp.status_code}, body: {resp.text}")
            if resp.status_code in [200, 201]:
                print(f"✅ Email successfully sent via Resend API to {to_emails}!")
                return True
            else:
                print(f"❌ Resend API error: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"❌ Exception calling Resend API: {e}")

    # 2. Option: SMTP Server fallback
    smtp_server = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    smtp_port = int(os.environ.get('SMTP_PORT', '587'))
    smtp_user = os.environ.get('SMTP_USER', '')
    smtp_password = os.environ.get('SMTP_PASSWORD', '')

    if smtp_user and smtp_password:
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = smtp_user
            msg['To'] = ", ".join(to_emails)
            msg['Subject'] = 'YouTube playlist watcher'

            msg.attach(MIMEText(f"{playlist_title}\n\n{text_content}", 'plain', 'utf-8'))
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))

            server = smtplib.SMTP(smtp_server, smtp_port, timeout=15)
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
            server.quit()
            print(f"✅ Direct SMTP email successfully sent to {to_emails}")
            return True
        except Exception as e:
            print(f"❌ Error sending SMTP email: {e}")
            return False

    print(f"ℹ️ No email provider configured (set RESEND_API_KEY in GitHub Secrets).")
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
        if not target_emails:
            target_emails = ["tamas.duffek@gmail.com"]

        print(f"\n==========================================")
        print(f"Processing playlist: {title} ({pid})")
        print(f"==========================================")

        # 1. Dump current playlist state
        code, out, err = run_command(f'python youtube_playlist_watcher.py --playlist-id "{pid}" dump --youtube-api-key "{api_key}"')
        if code != 0:
            print(f"Error dumping playlist {pid}: {err}")
            continue

        # 2. Compare with previous dump
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
            all_changes.append(f"{title}\n{out}\n{'-'*40}")
            send_email_notification(
                target_emails,
                'YouTube playlist watcher',
                out,
                playlist_title=title,
                playlist_id=pid
            )

        # 3. Purge old dumps
        run_command(f'python youtube_playlist_watcher.py --playlist-id "{pid}" purge-dumps --keep-count 30')

    # If force test mode is requested
    if force_test:
        test_msg = "<b>Törölt videó</b>\nThe Cure - Burn 1994 HQ (The Crow)\nhttps://www.youtube.com/results?search_query=The+Cure+-+Burn+1994+HQ\nPozíció a listán: 42."
        print("\n--- Generating Clean Test Alert ---")
        all_changes.append(f"Teszt\n{test_msg}")
        for item in playlists:
            target_emails = item.get('emails', [])
            if not target_emails:
                target_emails = ["tamas.duffek@gmail.com"]
            title = item.get('title', 'Saját teszt lista')
            pid = item.get('id', '')
            send_email_notification(
                target_emails,
                'YouTube playlist watcher',
                test_msg,
                playlist_title=title,
                playlist_id=pid
            )

    # Save latest status summary for Web UI
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(status_data, f, indent=2, ensure_ascii=False)

    # Save combined changes report for GitHub Issue notification
    if all_changes:
        with open('changes_report.txt', 'w', encoding='utf-8') as f:
            f.write("\n\n".join(all_changes))

if __name__ == '__main__':
    main()
