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
DEBUG_FILE = 'debug_log.txt'

def log_msg(msg):
    print(msg, flush=True)
    try:
        with open(DEBUG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass

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
        log_msg(f"Could not fetch title for playlist {playlist_id}: {e}")
    return f"Playlist ({playlist_id[:8]}...)"

def load_config(api_key=""):
    playlists_from_config = []
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                playlists_from_config = cfg.get('playlists', [])
        except Exception as e:
            log_msg(f"Error reading {CONFIG_FILE}: {e}")
    
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
            {"id": "PL46850C6F5BF668FE", "title": "BS koncert", "emails": ["tamas.duffek@gmail.com"]}
        ]
    }

def run_command(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
    return result.returncode, result.stdout, result.stderr

def send_combined_email(recipient_email, subject, html_content, plain_content):
    # 1. Primary Option: Direct SMTP (Gmail App Password)
    smtp_server = (os.environ.get('SMTP_SERVER', '').strip()) or 'smtp.gmail.com'
    
    raw_port = os.environ.get('SMTP_PORT', '').strip()
    smtp_port = int(raw_port) if (raw_port and raw_port.isdigit()) else 587

    smtp_user = os.environ.get('SMTP_USER', '').strip()
    smtp_password = os.environ.get('SMTP_PASSWORD', '').strip().replace(" ", "")

    log_msg(f"Initiating send_combined_email to: '{recipient_email}' (SMTP_USER configured: '{smtp_user}', SMTP_SERVER: '{smtp_server}:{smtp_port}')")

    if smtp_user and smtp_password:
        log_msg(f"Connecting to Gmail SMTP server {smtp_server}:{smtp_port} for {recipient_email}...")
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = f"YouTube watcher <{smtp_user}>"
            msg['To'] = recipient_email
            msg['Subject'] = subject

            msg.attach(MIMEText(plain_content, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))

            server = smtplib.SMTP(smtp_server, smtp_port, timeout=25)
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
            server.quit()
            log_msg(f"✅ Combined email successfully sent via Gmail SMTP to {recipient_email}!")
            return True
        except Exception as e:
            log_msg(f"⚠️ SMTP send failed ({e}), falling back to Resend API...")

    # 2. Fallback Option: Resend API
    resend_key = os.environ.get('RESEND_API_KEY', '').strip()
    from_email = (os.environ.get('FROM_EMAIL', '').strip()) or 'YouTube watcher <onboarding@resend.dev>'

    if resend_key:
        log_msg(f"Calling Resend API fallback for {recipient_email} from '{from_email}'...")
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
            log_msg(f"Resend HTTP Status: {resp.status_code}, Response: {resp.text}")
            if resp.status_code in [200, 201]:
                log_msg(f"✅ Combined email successfully sent via Resend to {recipient_email}!")
                return True
            else:
                log_msg(f"❌ Resend API error: {resp.status_code} - {resp.text}")
        except Exception as e:
            log_msg(f"❌ Exception calling Resend API: {e}")
    else:
        log_msg("⚠️ Neither SMTP credentials nor RESEND_API_KEY succeeded.")

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

        # Ignore regional restriction blocks (they are playable in Europe/Hungary)
        if line.startswith('IS BLOCKED IN REGION'):
            continue

        # REMOVED / DELETED
        if line.startswith('REMOVED:') or line.startswith('DELETED:'):
            m_pos = re.search(r'(?:was\s+)?(\d+)(?:th|st|nd|rd)?\s+video', line)
            pos_str = f"Pozíció a listán: {m_pos.group(1)}." if m_pos else ""
            
            clean_line = re.sub(r'^(?:REMOVED|DELETED):\s*', '', line)
            clean_line = re.sub(r'https?://\S+', '', clean_line)
            clean_line = re.sub(r'\((?:was\s+)?\d+(?:th|st|nd|rd)?\s+video[^)]*\)', '', clean_line)
            title = clean_line.strip()
            if title == 'NOT_FOUND':
                title = "Korábban törölt videó"
            
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
            if title == 'NOT_FOUND':
                title = "Korábban priváttá tett videó"
            alert_parts = ["<b>Privát videó</b>", title]
            if pos_str:
                alert_parts.append(pos_str)
            alerts.append("\n".join(alert_parts))

    return "\n\n".join(alerts)

def main():
    # Clear debug log for this run
    with open(DEBUG_FILE, 'w', encoding='utf-8') as f:
        f.write(f"=== Runner Execution Log at {datetime.now(timezone.utc).isoformat()} ===\n")

    api_key = os.environ.get('YOUTUBE_API_KEY', '').strip()
    force_test_raw = os.environ.get('FORCE_TEST_ALERT', 'false').strip()
    force_test = force_test_raw.lower() in ['true', '1', 'yes']

    log_msg(f"Starting runner with force_test={force_test} (raw: '{force_test_raw}')")

    config = load_config(api_key)
    playlists = config.get('playlists', [])
    
    current_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    status_data = {
        "last_run": current_utc,
        "results": []
    }

    all_changes = []
    pending_notifications_by_email = {}

    for item in playlists:
        pid = item['id']
        title = item.get('title', pid)
        target_emails = item.get('emails', [])
        if not target_emails:
            target_emails = ["tamas.duffek@gmail.com"]

        log_msg(f"Processing playlist: {title} ({pid})")

        if api_key:
            # 1. Dump current playlist state
            code, out, err = run_command(f'python youtube_playlist_watcher.py --playlist-id "{pid}" dump --youtube-api-key "{api_key}"')
            if code != 0:
                log_msg(f"Error dumping playlist {pid}: {err}")
            else:
                # 2. Compare with previous dump
                code, out, err = run_command(f'python youtube_playlist_watcher.py --playlist-id "{pid}" compare SECOND_TO_LAST LATEST --alert-on DELETED,REMOVED,IS_PRIVATE')
                
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
                    log_msg(f"⚠️ Change detected in '{title}'!")
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
            log_msg("YOUTUBE_API_KEY not set, skipping dump/compare.")

    # 4. Dispatch combined email per recipient if real changes detected
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

        log_msg(f"Sending live combined email to {email} with {len(items)} alert(s)...")
        send_combined_email(email, 'YouTube playlist watcher', full_html, full_plain)

    # 5. If force test mode is explicitly requested
    if force_test:
        target_email = os.environ.get('TEST_EMAIL', '').strip()
        target_title = os.environ.get('TEST_TITLE', '').strip() or "BS koncert"

        if not target_email:
            target_email = "tamas.duffek@gmail.com"

        test_msg = "Nincs változás"

        test_html = f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"></head>
        <body style="font-family: Arial, sans-serif; color: #111827; line-height: 1.6; font-size: 15px; padding: 10px;">
            <div style="font-size: 16px; font-weight: bold; margin-bottom: 6px;">{target_title}</div>
            <div>{test_msg}</div>
        </body>
        </html>
        """
        log_msg(f"Executing targeted test email to: '{target_email}' for '{target_title}'...")
        send_combined_email(target_email, 'YouTube playlist watcher', test_html, f"{target_title}\n{test_msg}")

    # Save latest status summary for Web UI
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump(status_data, f, indent=2, ensure_ascii=False)

    if all_changes:
        with open('changes_report.txt', 'w', encoding='utf-8') as f:
            f.write("\n\n".join(all_changes))

if __name__ == '__main__':
    main()
