from flask import Flask, render_template, make_response, request, redirect, url_for, flash, send_from_directory
import datetime
import subprocess # curlコマンド実行のために使用
import os
import re

app = Flask(__name__)
app.secret_key = 'your_super_secret_key_for_flash_messages' # 本番では必ず変更してください

DOWNLOAD_DIR = 'downloads'
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# ... (既存の / と /set_poke_dl_cookie, /about, /agree_terms, /download ルートは変更なし) ...

@app.route('/process_download', methods=['POST'])
def process_download():
    """動画のダウンロード処理を実行"""
    video_page_url = request.form.get('video_url')
    if not video_page_url:
        flash('動画URLを入力してください。', 'error')
        return redirect(url_for('download_page'))

    # 1. 'https://www.youtubep.com/watch?v=id' からIDを抽出
    match = re.search(r'watch\?v=([^&]+)', video_page_url)
    if not match:
        flash('動画ページのURL形式が不正です。例: https://www.youtubep.com/watch?v=YOUR_ID', 'error')
        return redirect(url_for('download_page'))
    video_id = match.group(1)

    nadeko_endpoints = [
        f'https://inv-us2-c.nadeko.net/latest_version?id={video_id}&itag=18&check',
        f'https://inv-ca1-c.nadeko.net/latest_version?id={video_id}&itag=18&check',
        f'https://inv-eu3-c.nadeko.net/latest_version?id={video_id}&itag=18&check',
    ]

    best_video_url = None
    max_content_length = -1
    full_verbose_log = [] # 全てのcurl -v の詳細ログを保存

    full_verbose_log.append(f"--- 処理開始: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    full_verbose_log.append(f"入力された動画ページURL: {video_page_url}")
    full_verbose_log.append(f"抽出された動画ID: {video_id}\n")

    # 2. 3つのNadekoエンドポイントをチェックし、最大コンテンツ長のものを選択
    # curlでContent-Lengthとリダイレクト先を正確に取得するため、HEADリクエストを使用
    for endpoint_url in nadeko_endpoints:
        try:
            # -D - でヘッダーをstderrに出力 (これはHEADには適用しにくいので、-vでまとめて取得)
            # Content-Lengthを得るためにヘッドリクエスト、またはフルダウンロードしてサイズを測る
            # ここではシンプルに-sLでリダイレクト先を取得し、その後でサイズを確認する
            # curl -sL -w "%{url_effective}\n" [URL] で最終的なURLを取得するのが一般的
            
            # HEADリクエストでヘッダーのみを取得し、Content-Lengthを推測
            # --silent: 進捗バーやエラーメッセージを表示しない
            # --location: リダイレクトを追跡
            # --output /dev/null: コンテンツを破棄
            # --write-out "%{url_effective}\n": 最終的なURLを標準出力に出力
            # --dump-header -: ヘッダー情報を標準出力に出力
            
            # ヘッダー情報を取得するためのcurlコマンド
            header_cmd = ['curl', '-sL', '-D', '-', '-o', '/dev/null', endpoint_url]
            header_result = subprocess.run(header_cmd, capture_output=True, text=True, check=False)
            
            headers = header_result.stdout
            
            # 最終的なURLを取得 (Content-Lengthを測るために使う)
            effective_url_cmd = ['curl', '-sL', '-w', '%{url_effective}', '-o', '/dev/null', endpoint_url]
            effective_url_result = subprocess.run(effective_url_cmd, capture_output=True, text=True, check=True)
            effective_url = effective_url_result.stdout.strip()

            content_length = -1
            # ヘッダーからContent-Lengthを抽出
            for line in headers.splitlines():
                if line.lower().startswith('content-length:'):
                    try:
                        content_length = int(line.split(':')[1].strip())
                        break
                    except ValueError:
                        pass # 無効なContent-Length

            full_verbose_log.append(f"--- エンドポイントチェック: {endpoint_url} ---")
            full_verbose_log.append(f"  実行コマンド: {' '.join(header_cmd)}")
            full_verbose_log.append(f"  最終リダイレクトURL: {effective_url}")
            full_verbose_log.append(f"  Content-Length: {content_length if content_length != -1 else '不明'}")
            full_verbose_log.append("  取得ヘッダー:\n" + headers + "\n")
            
            if content_length > max_content_length:
                max_content_length = content_length
                best_video_url = effective_url

        except subprocess.CalledProcessError as e:
            full_verbose_log.append(f"--- エンドポイントエラー: {endpoint_url} ---")
            full_verbose_log.append(f"  curlコマンド実行エラー: {e}")
            full_verbose_log.append(f"  stderr: {e.stderr}\n")
        except Exception as e:
            full_verbose_log.append(f"--- エンドポイント予期せぬエラー: {endpoint_url} ---")
            full_verbose_log.append(f"  エラー: {e}\n")

    if not best_video_url:
        flash('動画のダウンロードURLを見つけることができませんでした。URLを確認してください。', 'error')
        # ログも保存しておく
        log_filename_error = f"download_log_error_{video_id}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.txt"
        log_filepath_error = os.path.join(DOWNLOAD_DIR, log_filename_error)
        with open(log_filepath_error, 'w', encoding='utf-8') as f:
            f.write("\n".join(full_verbose_log))
        return render_template('y/dl.html', log_filename=log_filename_error)

    # 最終的な最適なURLに対して、curl -L -v で詳細ログを取得
    final_curl_cmd = ['curl', '-L', '-v', best_video_url, '-o', '/dev/null'] # コンテンツはダウンロードせずログのみ
    try:
        # stderrに詳細ログが出るため、stderrをキャプチャ
        final_curl_result = subprocess.run(final_curl_cmd, capture_output=True, text=True, check=True)
        final_verbose_output = final_curl_result.stderr # -v の出力はstderrに出る

        full_verbose_log.append(f"--- 最適なURLに対する詳細curlログ: {best_video_url} ---")
        full_verbose_log.append(f"  実行コマンド: {' '.join(final_curl_cmd)}")
        full_verbose_log.append(final_verbose_output) # ここに詳細ログを追加
        full_verbose_log.append("\n")

    except subprocess.CalledProcessError as e:
        full_verbose_log.append(f"--- 最終curlコマンドエラー: {best_video_url} ---")
        full_verbose_log.append(f"  エラー: {e}")
        full_verbose_log.append(f"  stderr: {e.stderr}\n")
        # エラーログも保存し、最終URLが取得できなかったことを示す
        flash('最終的な詳細ログの取得中にエラーが発生しました。', 'warning')

    # ログファイルを保存
    log_filename = f"download_log_{video_id}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}.txt"
    log_filepath = os.path.join(DOWNLOAD_DIR, log_filename)
    with open(log_filepath, 'w', encoding='utf-8') as f:
        f.write("\n".join(full_verbose_log))

    flash('動画ダウンロードURLを取得しました。', 'success')
    return render_template('y/dl.html',
                           final_video_url=best_video_url,
                           log_filename=log_filename)

@app.route('/download_log/<filename>')
def download_log(filename):
    """保存されたログファイルをダウンロード可能にする"""
    return send_from_directory(DOWNLOAD_DIR, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(debug=True)
