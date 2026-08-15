#!/usr/bin/env python3
import os
import sys
import re
import json
import smtplib
import requests
import subprocess
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

CONFIG_FILE = 'playlists_config.json'
STATUS_FILE = 'latest_status.json'

def get_youtube_playlist_title(api_key, playlist_id):
    try:
        url = f"https://www.googleapis.com/youtube/v3/playlists?part=snippet&id={playlist_id}&key={api_key}"
        resp = requests.get(url, timeout=10)
        if resp.ok:
            data = resp.json()
            items = data.get('items', [])
            if items:
                return items[0]['snippet']['title']
    except Exception as e:
        print(f"Could not fetch title for playlist {playlist_id}: {e}")
    return f"Playlist ({playlist_id[:8]}...)"

def load_config(api_key=""):
    playlists_from_config = []
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                playlists_from_config = cfg.get('playlists', [])
        except Exception as e:
            print(f"Error reading {CONFIG_FILE}: {e}")
    
    env_playlists = os.environ.get('PLAYLIST_ID', '')
    p_ids = [p.strip() for p in env_playlists.split(',') if p.strip()]
    
    existing_ids = {p['id'] for p in playlists_from_config}
    for pid in p_ids:
        if pid not in existing_ids:
            title = get_youtube_playlist_title(api_key, pid) if api_key else pid
            playlists_from_config.append({
                "id": pid,
                "title": title,
                "emails": ["tamas.duffek@gmail.com"]
            })
            existing_ids.add(pid)

    return {
        "pin_hash": "03ac674216f3e15c761ee1a5e255f067953623c8b388b4459e13f978d7c846f4",
        "playlists": playlists_from_config if playlists_from_config else [
            {"id": "PL46850C6F5BF668FE", "title": "próba lista", "emails": ["tamas.duffek@gmail.com"]}
        ]
    }

def run_command(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    return result.returncode, result.stdout, result.stderr

def send_combined_email(recipient_email, subject, html_content, plain_content):
    resend_key = os.environ.get('RESEND_API_KEY', '').strip()
    from_email = os.environ.get('FROM_EMAIL', 'YouTube watcher <onboarding@resend.dev>').strip() or 'YouTube watcher <onboarding@resend.dev>'

    if resend_key:
        print(f"Sending combined email via Resend API to {recipient_email} from '{from_email}'...")
        try:
            resp = requests.post(
                'https://api.resend.com/emails',
                headers={
                    'Authorization': f'Bearer {resend_key}',
                    'Content-Type': 'application/json'
                },
                json={
                    'from': from_email,
                    'to': [recipient_email],
                    'subject': subject,
                    'html': html_content,
                    'text': plain_content
                },
                timeout=15
            )
            print(f"Resend HTTP Status: {resp.status_code}, Body: {resp.text}")
            if resp.status_code in [200, 201]:
                print(f"✅ Combined email successfully sent via Resend to {recipient_email}!")
                return True
            else:
                print(f"❌ Resend API error: {resp.status_code} - {resp.text}")
        except Exception as e:
            print(f"❌ Exception calling Resend API: {e}")

    return False

def format_hungarian_alert(raw_output):
    alerts = []
    lines = raw_output.split('\n')
    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Ignore ADDED items completely (no alert on new additions)
        if line.startswith('ADDED:'):
            continue

        # REMOVED / DELETED
        if line.startswith('REMOVED:') or line.startswith('DELETED:'):
            m_pos = re.search(r'(?:was\s+)?(\d+)(?:th|st|nd|rd)?\s+video', line)
            pos_str = f"Pozíció a listán: {m_pos.group(1)}." if m_pos else ""
            
            clean_line = re.sub(r'^(?:REMOVED|DELETED):\s*', '', line)
            clean_line = re.sub(r'https?://\S+', '', clean_line)
            clean_line = re.sub(r'\((?:was\s+)?\d+(?:th|st|nd|rd)?\s+video[^)]*\)', '', clean_line)
            title = clean_line.strip()
            
            alert_parts = ["<b>Törölt videó</b>", title]
            if pos_str:
                alert_parts.append(pos_str)
            alerts.append("\n".join(alert_parts))

        elif line.startswith('IS PRIVATE:'):
            m_pos = re.search(r'(\d+)(?:th|st|nd|rd)?\s+video', line)
            pos_str = f"Pozíció a listán: {m_pos.group(1)}." if m_pos else ""
            clean_line = re.sub(r'^IS PRIVATE:\s*', '', line)
            clean_line = re.sub(r'https?://\S+', '', clean_line)
            clean_line = re.sub(r'\(\d+(?:th|st|nd|rd)?\s+video[^)]*\)', '', clean_line)
            title = clean_line.strip()
            alert_parts = ["<b>Privát videó</b>", title]
            if pos_str:
                alert_parts.append(pos_str)
            alerts.append("\n".join(alert_parts))

        elif line.startswith('IS BLOCKED IN REGION'):
            clean_line = re.sub(r'^IS BLOCKED IN REGION[^:]*:\s*', '', line)
            clean_line = re.sub(r'https?://\S+', '', clean_line)
            title = clean_line.strip()
            alerts.append(f"<b>Blokkolt videó</b>\n{title}")

    return "\n\n".join(alerts)

def main():
    api_key = os.environ.get('YOUTUBE_API_KEY', '').strip()
    force_test = os.environ.get('FORCE_TEST_ALERT', 'false').lower() in ['true', '1', 'yes']

    config = load_config(api_key)
    playlists = config.get('playlists', [])
    
    current_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    status_data = {
        "last_run": current_utc,
        "results": []
    }

    all_changes = []
    # Group alerts by recipient email: email -> list of (playlist_title, hungarian_alert)
    pending_notifications_by_email = {}

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
                
                hungarian_alert = format_hungarian_alert(out) if out else ""
                has_actual_alert = bool(hungarian_alert)
                
                playlist_result = {
                    "id": pid,
                    "title": title,
                    "has_changes": has_actual_alert,
                    "report": hungarian_alert if has_actual_alert else "Minden videó elérhető és változatlan."
                }
                status_data["results"].append(playlist_result)

                if has_actual_alert:
                    print(f"⚠️ Értesítendő törlés/változás a '{title}' listában!")
                    all_changes.append(f"{title}\n{hungarian_alert}\n{'-'*40}")
                    for email in target_emails:
                        email = email.strip()
                        if email:
                            if email not in pending_notifications_by_email:
                                pending_notifications_by_email[email] = []
                            pending_notifications_by_email[email].append((title, hungarian_alert))

                # 3. Purge old dumps
                run_command(f'python youtube_playlist_watcher.py --playlist-id "{pid}" purge-dumps --keep-count 30')
        else:
            print("YOUTUBE_API_KEY not set, skipping dump/compare.")

    # 4. Dispatch exactly ONE combined email per recipient
    for email, items in pending_notifications_by_email.items():
        combined_html_blocks = []
        combined_plain_blocks = []
        for p_title, p_alert in items:
            p_alert_html = p_alert.replace("\n", "<br>")
            combined_html_blocks.append(f"""
            <div>
                <div style="font-size: 16px; font-weight: bold; margin-bottom: 6px;">{p_title}</div>
                <div>{p_alert_html}</div>
            </div>
            """)
            combined_plain_blocks.append(f"{p_title}\n{p_alert}")

        separator_html = '<hr style="border: 0; border-top: 1px solid #cbd5e1; margin: 20px 0;">'
        full_html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: Arial, sans-serif; color: #111827; line-height: 1.6; font-size: 15px; padding: 10px;">
            {separator_html.join(combined_html_blocks)}
        </body>
        </html>
        """
        full_plain = ("\n\n" + "-"*40 + "\n\n").join(combined_plain_blocks)

        print(f"\n📧 Sending 1 combined email to {email} with {len(items)} playlist alert(s)...")
        send_combined_email(email, 'YouTube playlist watcher', full_html, full_plain)

    # If force test mode is explicitly requested
    if force_test:
        test_msg = "<b>Törölt videó</b>\nThe Cure - Burn 1994 HQ (The Crow)\nPozíció a listán: 42."
        dynamic_title = playlists[0].get('title', 'Saját lista') if playlists else "Saját lista"
        dynamic_emails = playlists[0].get('emails', ["tamas.duffek@gmail.com"]) if playlists else ["tamas.duffek@gmail.com"]
        for email in dynamic_emails:
            test_html = f"""
            <!DOCTYPE html>
            <html>
            <head><meta charset="utf-8"></head>
            <body style="font-family: Arial, sans-serif; color: #111827; line-height: 1.6; font-size: 15px; padding: 10px;">
                <div style="font-size: 16px; font-weight: bold; margin-bottom: 6px;">{dynamic_title}</div>
                <div>{test_msg.replace(chr(10), '<br>')}</div>
            </body>
            </html>
            """
            send_combined_email(email, 'YouTube playlist watcher', test_html, f"{dynamic_title}\n{test_msg}")

    # Save latest status summary for Web UI
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(status_data, f, indent=2, ensure_ascii=False)

    if all_changes:
        with open('changes_report.txt', 'w', encoding='utf-8') as f:
            f.write("\n\n".join(all_changes))

if __name__ == '__main__':
    main()
