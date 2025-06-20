import os
import re
import datetime
import subprocess # curlコマンド実行のために使用

from flask import Flask, render_template, make_response, request, redirect, url_for, flash, send_from_directory

app = Flask(__name__)
# IMPORTANT: Change this secret key to a strong, random value in production.
# Example: os.urandom(24).hex()
app.secret_key = 'your_super_secret_key_for_flash_messages_do_not_use_in_prod'

# Directory to save downloaded logs.
# IMPORTANT FIX: Changed from 'downloads' to '/tmp' to work in read-only file systems (e.g., serverless environments).
# Files in /tmp are temporary and will be lost after the function execution.
DOWNLOAD_DIR = '/tmp'
os.makedirs(DOWNLOAD_DIR, exist_ok=True) # Create the directory if it doesn't exist


@app.route('/')
def index():
    """
    Renders the index page.
    This page provides a brief introduction and a "Next" button.
    """
    return render_template('y/index.html')


@app.route('/set_poke_dl_cookie')
def set_poke_dl_cookie():
    """
    Sets the 'poke-dl' cookie (valid for 1 week) and redirects to the about page.
    This cookie indicates the user has passed the initial page.
    """
    resp = make_response(redirect(url_for('about')))
    # Set cookie to expire in 1 week
    expires = datetime.datetime.now() + datetime.timedelta(weeks=1)
    # httponly=True prevents client-side JavaScript from accessing the cookie, enhancing security
    resp.set_cookie('poke-dl', 'true', expires=expires, httponly=True)
    flash('「poke-dl」クッキーを1週間設定しました。', 'info')
    return resp


@app.route('/about')
def about():
    """
    Renders the about page, which contains the terms of service.
    Includes a checkbox for agreement and an "Agree" button.
    """
    return render_template('y/about.html')


@app.route('/agree_terms', methods=['POST'])
def agree_terms():
    """
    Processes the terms of service agreement.
    If the checkbox is ticked, sets the 'poke-yuki-dl' cookie (valid for 2 weeks)
    and redirects to the download page. Otherwise, shows an error message.
    """
    # Check if the agreement checkbox was ticked
    if request.form.get('agree_checkbox'):
        resp = make_response(redirect(url_for('download_page')))
        # Set cookie to expire in 2 weeks
        expires = datetime.datetime.now() + datetime.timedelta(weeks=2)
        resp.set_cookie('poke-yuki-dl', 'true', expires=expires, httponly=True)
        flash('「poke-yuki-dl」クッキーを2週間設定しました。利用規約に同意しました。', 'success')
        return resp
    else:
        # If agreement not checked, display an error and redirect back to the about page
        flash('利用規約への同意が必要です。', 'error')
        return redirect(url_for('about'))


@app.route('/download')
def download_page():
    """
    Renders the video download page.
    Access to this page requires both 'poke-dl' and 'poke-yuki-dl' cookies to be set to 'true'.
    """
    poke_dl_cookie = request.cookies.get('poke-dl')
    poke_yuki_dl_cookie = request.cookies.get('poke-yuki-dl')

    # Verify that both required cookies are present and set to 'true'
    if poke_dl_cookie == 'true' and poke_yuki_dl_cookie == 'true':
        return render_template('y/dl.html')
    else:
        # If cookies are missing, flash a warning and redirect to the index page
        flash('ダウンロードページにアクセスするには、トップページから進み、利用規約に同意してください。', 'warning')
        return redirect(url_for('index'))


@app.route('/process_download', methods=['POST'])
def process_download():
    """
    Processes the video download request.
    Extracts video ID from the input URL, queries Nadeko endpoints using curl,
    selects the best video URL based on Content-Length, and retrieves verbose curl logs.
    """
    video_page_url = request.form.get('video_url')
    if not video_page_url:
        flash('動画URLを入力してください。', 'error')
        return redirect(url_for('download_page'))

    # Extract video ID from URL like 'https://www.youtubep.com/watch?v=YOUR_ID'
    match = re.search(r'watch\?v=([^&]+)', video_page_url)
    if not match:
        flash('動画ページのURL形式が不正です。例: https://www.youtube.com/watch?v=YOUR_ID', 'error')
        return redirect(url_for('download_page'))
    video_id = match.group(1)

    # List of Nadeko inverse proxy endpoints to check
    nadeko_endpoints = [
        f'https://inv-us2-c.nadeko.net/latest_version?id={video_id}&itag=18&check',
        f'https://inv-ca1-c.nadeko.net/latest_version?id={video_id}&itag=18&check',
        f'https://inv-eu3-c.nadeko.net/latest_version?id={video_id}&itag=18&check',
    ]

    best_video_url = None
    max_content_length = -1
    all_verbose_logs = [] # List to store all verbose curl logs from each endpoint check

    all_verbose_logs.append(f"--- Processing Started: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    all_verbose_logs.append(f"Input Video Page URL: {video_page_url}")
    all_verbose_logs.append(f"Extracted Video ID: {video_id}\n")

    # Iterate through each Nadeko endpoint to find the one with the largest Content-Length
    for endpoint_url in nadeko_endpoints:
        try:
            # Use /dev/null for content output to avoid downloading the video itself during this check
            # Note: os.devnull works for Unix-like systems. For Windows, it's 'NUL'.
            # A robust solution for cross-platform might need conditional logic or tempfile module.
            curl_cmd = ['curl', '-L', '-v', '-o', os.devnull, endpoint_url]
            
            # Execute curl command, capturing its standard error (where -v output goes)
            # text=True decodes output as string, check=False prevents error on non-zero exit code
            result = subprocess.run(curl_cmd, capture_output=True, text=True, check=False, timeout=30)
            
            verbose_output = result.stderr # This contains the full -v log

            # Extract final effective URL from the verbose output
            # This regex looks for 'Location:' header on a new line during redirects, or the final URL if no redirect.
            # It's heuristic and might need adjustment based on actual curl output for specific servers.
            final_url_match = re.findall(r'^(?:<|>|\*)\s*(?:Location:|\* Connected to|\* Hostname was resolved to).*?(https?://[^\s]+)', verbose_output, re.MULTILINE)
            current_effective_url = final_url_match[-1] if final_url_match else endpoint_url # Last matched URL is likely the final one

            # Extract Content-Length from the verbose output (from the last response headers)
            content_length_match = re.findall(r'^<\s*content-length:\s*(\d+)', verbose_output, re.MULTILINE | re.IGNORECASE)
            current_content_length = int(content_length_match[-1]) if content_length_match else -1 # Last matched content-length

            all_verbose_logs.append(f"--- Endpoint Check: {endpoint_url} ---")
            all_verbose_logs.append(f"  Executed Command: {' '.join(curl_cmd)}")
            all_verbose_logs.append(f"  Final Redirect URL: {current_effective_url}")
            all_verbose_logs.append(f"  Content-Length: {current_content_length if current_content_length != -1 else 'Unknown'}")
            all_verbose_logs.append("  curl -v Detailed Log:\n" + verbose_output + "\n")

            # Update best_video_url if current endpoint has a larger content length
            if current_content_length > max_content_length:
                max_content_length = current_content_length
                best_video_url = current_effective_url

        except subprocess.TimeoutExpired:
            # Handle timeout errors for curl commands
            all_verbose_logs.append(f"--- Endpoint Error: {endpoint_url} ---")
            all_verbose_logs.append(f"  curl command timed out (after 30 seconds).\n")
        except Exception as e:
            # Catch any other unexpected errors during curl execution or parsing
            all_verbose_logs.append(f"--- Endpoint Unexpected Error: {endpoint_url} ---")
            all_verbose_logs.append(f"  Error: {e}\n")

    # If no suitable video URL was found after checking all endpoints
    if not best_video_url:
        flash('動画のダウンロードURLを見つけることができませんでした。URLを確認してください。', 'error')
        # Save the logs even in case of an error
        log_filename_error = f"download_log_error_{video_id}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.txt"
        log_filepath_error = os.path.join(DOWNLOAD_DIR, log_filename_error)
        with open(log_filepath_error, 'w', encoding='utf-8') as f:
            f.write("\n".join(all_verbose_logs))
        # Render the download page, but only provide the log file name, not a video URL
        return render_template('y/dl.html', log_filename=log_filename_error)

    # Save the collected verbose logs to a file
    log_filename = f"download_log_{video_id}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.txt"
    log_filepath = os.path.join(DOWNLOAD_DIR, log_filename)
    with open(log_filepath, 'w', encoding='utf-8') as f:
        f.write("\n".join(all_verbose_logs))

    flash('動画ダウンロードURLを取得しました。', 'success')
    # Render the download page, passing the best video URL and the log file name to the template
    return render_template('y/dl.html',
                           final_video_url=best_video_url,
                           log_filename=log_filename)


@app.route('/download_log/<filename>')
def download_log(filename):
    """
    Allows users to download the saved verbose log files.
    send_from_directory is used to prevent directory traversal attacks.
    """
    # Important: In serverless environments, files in /tmp are transient.
    # If logs need to persist, an external storage service (e.g., AWS S3) is required.
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)


if __name__ == '__main__':
    # Start the Flask development server.
    # For production deployment, use a WSGI server like Gunicorn or uWSGI.
    # debug=True should ONLY be used during development for automatic code reloading.
    app.run(debug=True)

