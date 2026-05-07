import json
import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import urllib3
from urllib3.filepost import encode_multipart_formdata

http = urllib3.PoolManager()
TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    "8707596426:AAHz8Q4uO7DmUu00fm9vQa-tC1-k1y3nb0E",
)
BASE_URL = f"https://api.telegram.org/bot{TOKEN}"

# 福利视频 Google 文件夹（任何人可查看时，搭配 GOOGLE_DRIVE_API_KEY 可列出文件）
WELFARE_DRIVE_FOLDER_ID = os.environ.get(
    "GOOGLE_DRIVE_WELFARE_FOLDER_ID",
    "1JaRTFoe-YKyksO1NxES5J-D9AdUUXCRQ",
)
WELFARE_FOLDER_URL = (
    f"https://drive.google.com/drive/folders/{WELFARE_DRIVE_FOLDER_ID}?usp=sharing"
)

# 课表介绍图片文件夹
SCHEDULE_DRIVE_FOLDER_ID = os.environ.get(
    "GOOGLE_DRIVE_SCHEDULE_FOLDER_ID",
    "1sjpjbWJdtAqeVk1YOTRqA9Y7cuf0W8sg",
)
SCHEDULE_FOLDER_URL = (
    f"https://drive.google.com/drive/folders/{SCHEDULE_DRIVE_FOLDER_ID}?usp=sharing"
)

# 地址导航：图片文件夹 + 说明文字档（.txt）
ADDRESS_NAV_DRIVE_FOLDER_ID = os.environ.get(
    "GOOGLE_DRIVE_ADDRESS_NAV_FOLDER_ID",
    "1fbTEOnepCit2OaDspg61qFM2ki_-Nceg",
)
ADDRESS_NAV_TEXT_FILE_ID = os.environ.get(
    "GOOGLE_DRIVE_ADDRESS_NAV_TEXT_FILE_ID",
    "1E89CLnAZ_rgg048QQ6F8nZBu0ioiRUvv",
)
ADDRESS_NAV_FOLDER_URL = (
    f"https://drive.google.com/drive/folders/{ADDRESS_NAV_DRIVE_FOLDER_ID}?usp=sharing"
)
ADDRESS_NAV_TEXT_FILE_URL = (
    f"https://drive.google.com/file/d/{ADDRESS_NAV_TEXT_FILE_ID}/view?usp=sharing"
)

_VIDEO_MIME_PREFIX = "video/"
_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi")
_IMAGE_MIME_PREFIX = "image/"
_IMAGE_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".bmp",
    ".tif",
    ".tiff",
    ".heic",
)


def _google_drive_api_key():
    """与 Drive list / 下载共用，避免各处读到不一致。"""
    return (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip() or None


def _mask_api_key(value):
    if not value:
        return "EMPTY"
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _redact_url_for_logs(url):
    """避免把 Google API key 完整写进 CloudWatch。"""
    if not url or "key=" not in url:
        return url
    parsed = urlparse(url)
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    masked = [(k, _mask_api_key(v) if k == "key" else v) for k, v in pairs]
    new_query = urlencode(masked)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
    )


def _is_video_file(name, mime_type):
    if mime_type and mime_type.startswith(_VIDEO_MIME_PREFIX):
        return True
    lower = (name or "").lower()
    return any(lower.endswith(ext) for ext in _VIDEO_EXTENSIONS)


def _is_image_file(name, mime_type):
    if mime_type:
        if mime_type.startswith(_IMAGE_MIME_PREFIX):
            return True
        if mime_type.startswith("application/vnd.google-apps."):
            return False
    lower = (name or "").lower()
    return any(lower.endswith(ext) for ext in _IMAGE_EXTENSIONS)


def _drive_direct_download_url(file_id):
    """给浏览器用；Telegram 服务器抓取时常拿到 HTML 确认页，易导致 WEBPAGE_MEDIA_EMPTY。"""
    return f"https://drive.google.com/uc?export=download&id={file_id}"


def _telegram_video_source_url(file_id):
    """
    Telegram 会用 HTTP GET 拉 URL 本体；须回传实际影片字节。
    有 API Key 时用 Drive v3 alt=media（公开文件 + API Key 可下载），比 uc?export=download 稳定。
    """
    api_key = _google_drive_api_key()
    if api_key:
        q = urlencode({"alt": "media", "key": api_key})
        return f"https://www.googleapis.com/drive/v3/files/{file_id}?{q}"
    return _drive_direct_download_url(file_id)


def list_drive_files_in_folder(folder_id, log_label):
    """列出文件夹内非删除文件（不含子文件夹结构递归）。"""
    api_key = _google_drive_api_key()
    if not api_key:
        print(f"Drive API key missing ({log_label}): GOOGLE_DRIVE_API_KEY is empty")
        return []

    params = {
        "q": f"'{folder_id}' in parents and trashed=false",
        "fields": "files(id,name,mimeType)",
        "key": api_key,
    }
    url = "https://www.googleapis.com/drive/v3/files?" + urlencode(params)
    safe_params = dict(params)
    safe_params["key"] = _mask_api_key(api_key)
    safe_url = "https://www.googleapis.com/drive/v3/files?" + urlencode(safe_params)
    print(f"Drive API list ({log_label}): folder_id={folder_id} url={safe_url}")
    response = http.request("GET", url)
    if response.status != 200:
        print(
            f"Drive API list failed ({log_label}): status={response.status} "
            f"body={response.data.decode('utf-8', errors='replace')}"
        )
        return []

    body = json.loads(response.data.decode("utf-8"))
    files = body.get("files") or []
    print(f"Drive API list success ({log_label}): total_files={len(files)}")
    return files


def list_welfare_videos_from_drive():
    """使用 Drive API v3 列出福利文件夹内影片。"""
    files = list_drive_files_in_folder(WELFARE_DRIVE_FOLDER_ID, "welfare")
    out = []
    for f in files:
        fid, name, mime = f.get("id"), f.get("name"), f.get("mimeType")
        if _is_video_file(name, mime):
            out.append({"id": fid, "name": name})
    print(f"Drive API filtered videos: total_videos={len(out)}")
    return out


def list_schedule_images_from_drive():
    """课表文件夹内图片，按文件名排序（不分大小写）。"""
    files = list_drive_files_in_folder(SCHEDULE_DRIVE_FOLDER_ID, "schedule")
    out = []
    for f in files:
        fid, name, mime = f.get("id"), f.get("name"), f.get("mimeType")
        if _is_image_file(name, mime):
            out.append({"id": fid, "name": name})
    out.sort(key=lambda x: str(x.get("name") or "").lower())
    print(f"Schedule images sorted by name: total={len(out)}")
    return out


def list_address_nav_images_from_drive():
    """地址导航文件夹内图片，按文件名排序（不分大小写）。"""
    files = list_drive_files_in_folder(ADDRESS_NAV_DRIVE_FOLDER_ID, "address_nav")
    out = []
    for f in files:
        fid, name, mime = f.get("id"), f.get("name"), f.get("mimeType")
        if _is_image_file(name, mime):
            out.append({"id": fid, "name": name})
    out.sort(key=lambda x: str(x.get("name") or "").lower())
    print(f"Address nav images sorted by name: total={len(out)}")
    return out


def welfare_videos_from_env_ids():
    """逗号分隔的文件 ID，例如 DRIVE_VIDEO_FILE_IDS=id1,id2（不需 Drive list API）。"""
    raw = os.environ.get("DRIVE_VIDEO_FILE_IDS", "").strip()
    if not raw:
        return []
    parsed = [{"id": x.strip(), "name": None} for x in raw.split(",") if x.strip()]
    print(f"Fallback env file ids (video): total={len(parsed)}")
    return parsed


def schedule_photos_from_env_ids():
    """课表图片文件 ID，逗号分隔；顺序为用户填写顺序（无文件名时无法按名排序）。"""
    raw = os.environ.get("DRIVE_SCHEDULE_PHOTO_FILE_IDS", "").strip()
    if not raw:
        return []
    parsed = [{"id": x.strip(), "name": None} for x in raw.split(",") if x.strip()]
    parsed.sort(key=lambda x: x.get("id") or "")
    print(f"Fallback env file ids (schedule photos): total={len(parsed)}")
    return parsed


def address_nav_photos_from_env_ids():
    raw = os.environ.get("DRIVE_ADDRESS_NAV_PHOTO_FILE_IDS", "").strip()
    if not raw:
        return []
    parsed = [{"id": x.strip(), "name": None} for x in raw.split(",") if x.strip()]
    parsed.sort(key=lambda x: x.get("id") or "")
    print(f"Fallback env file ids (address nav photos): total={len(parsed)}")
    return parsed


def get_welfare_video_list():
    videos = list_welfare_videos_from_drive()
    if not videos:
        videos = welfare_videos_from_env_ids()
    out = []
    for idx, v in enumerate(videos, start=1):
        fid = v.get("id")
        if not fid:
            print(f"Skip invalid video entry: index={idx} entry={v}")
            continue
        out.append(v)
    print(f"Welfare video list count: {len(out)}")
    return out


def get_schedule_image_list():
    images = list_schedule_images_from_drive()
    if not images:
        images = schedule_photos_from_env_ids()
    out = []
    for idx, v in enumerate(images, start=1):
        fid = v.get("id")
        if not fid:
            print(f"Skip invalid schedule image entry: index={idx} entry={v}")
            continue
        out.append(v)
    # 有文件名时再按名排序（Drive 已排序；env fallback 仅有 id 时上面已 sort id）
    if all(v.get("name") for v in out):
        out.sort(key=lambda x: str(x.get("name") or "").lower())
    print(f"Schedule image list count: {len(out)}")
    return out


def get_address_nav_image_list():
    images = list_address_nav_images_from_drive()
    if not images:
        images = address_nav_photos_from_env_ids()
    out = []
    for idx, v in enumerate(images, start=1):
        fid = v.get("id")
        if not fid:
            print(f"Skip invalid address nav image entry: index={idx} entry={v}")
            continue
        out.append(v)
    if all(v.get("name") for v in out):
        out.sort(key=lambda x: str(x.get("name") or "").lower())
    print(f"Address nav image list count: {len(out)}")
    return out


def _safe_video_filename(name, file_id):
    base = (name or "").strip() or f"{file_id}.mp4"
    base = base.replace("/", "_").replace("\\", "_")
    return base


def _guess_video_mime(filename):
    lower = (filename or "").lower()
    if lower.endswith(".mp4"):
        return "video/mp4"
    if lower.endswith(".mov"):
        return "video/quicktime"
    if lower.endswith(".webm"):
        return "video/webm"
    if lower.endswith(".mkv"):
        return "video/x-matroska"
    if lower.endswith(".m4v"):
        return "video/x-m4v"
    return "video/mp4"


def _safe_image_filename(name, file_id):
    base = (name or "").strip() or f"{file_id}.jpg"
    base = base.replace("/", "_").replace("\\", "_")
    return base


def _guess_image_mime(filename):
    lower = (filename or "").lower()
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".bmp"):
        return "image/bmp"
    if lower.endswith((".tif", ".tiff")):
        return "image/tiff"
    if lower.endswith(".heic"):
        return "image/heic"
    return "image/jpeg"


def fetch_drive_file_bytes(file_id):
    """用 Drive v3 alt=media 在 Lambda 内下载（需 GOOGLE_DRIVE_API_KEY）。"""
    api_key = _google_drive_api_key()
    if not api_key:
        return None, "missing_api_key"
    q = urlencode({"alt": "media", "key": api_key})
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}?{q}"
    resp = http.request("GET", url)
    if resp.status != 200:
        snippet = resp.data[:500].decode("utf-8", errors="replace")
        return None, f"http_{resp.status} {snippet}"
    return resp.data, None


_TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def _split_telegram_messages(text):
    """Telegram 单条消息上限 4096 字符。"""
    if not text:
        return []
    chunks = []
    s = text
    while s:
        chunks.append(s[:_TELEGRAM_MAX_MESSAGE_LENGTH])
        s = s[_TELEGRAM_MAX_MESSAGE_LENGTH:]
    return chunks


def fetch_address_nav_text():
    """下载地址导航说明 txt，解码为字符串。"""
    blob, err = fetch_drive_file_bytes(ADDRESS_NAV_TEXT_FILE_ID)
    if err:
        print(f"Address nav text file download failed: {err}")
        return None
    try:
        return blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        return blob.decode("utf-8", errors="replace")


def fetch_drive_video_bytes(file_id):
    return fetch_drive_file_bytes(file_id)


def build_welfare_video_media_entries(videos_meta=None):
    """无 API Key 时后备：把公开 URL 丢给 Telegram 去抓（较易 MEDIA_INVALID / EMPTY）。"""
    media = []
    source_list = videos_meta if videos_meta is not None else get_welfare_video_list()
    for idx, v in enumerate(source_list, start=1):
        fid = v.get("id")
        source_url = _telegram_video_source_url(fid)
        media.append({"type": "video", "media": source_url})
        print(
            "Prepared welfare video (URL mode): "
            f"index={idx} file_id={fid} name={v.get('name')} source={_redact_url_for_logs(source_url)}"
        )
    print(f"Prepared media entries (URL mode): total={len(media)}")
    return media


def send_multipart(method, fields):
    body, content_type = encode_multipart_formdata(fields)
    url = f"{BASE_URL}/{method}"
    response = http.request(
        "POST", url, body=body, headers={"Content-Type": content_type}
    )
    print(f"Telegram API Response ({method}): {response.data.decode('utf-8')}")
    return response


def send_video_multipart(chat_id, filename, blob, mime):
    fields = [
        ("chat_id", str(chat_id)),
        ("supports_streaming", "true"),
        ("video", (filename, blob, mime)),
    ]
    return send_multipart("sendVideo", fields)


def send_photo_multipart(chat_id, filename, blob, mime):
    fields = [
        ("chat_id", str(chat_id)),
        ("photo", (filename, blob, mime)),
    ]
    return send_multipart("sendPhoto", fields)


def send_media_group_videos_multipart(chat_id, items):
    """
    items: list of (filename, bytes, mime)，长度 2～10（Telegram sendMediaGroup 规定至少 2 个）。
    """
    if not (2 <= len(items) <= 10):
        raise ValueError("sendMediaGroup 需要 2–10 个媒体")

    media_spec = []
    fields = [
        ("chat_id", str(chat_id)),
    ]
    for i, (filename, blob, mime) in enumerate(items):
        attach = f"vid{i}"
        media_spec.append({"type": "video", "media": f"attach://{attach}"})
        fields.append((attach, (filename, blob, mime)))
    fields.insert(1, ("media", json.dumps(media_spec)))
    return send_multipart("sendMediaGroup", fields)


def send_media_group_photos_multipart(chat_id, items):
    """items: (filename, bytes, mime)，2～10 张。"""
    if not (2 <= len(items) <= 10):
        raise ValueError("sendMediaGroup 需要 2–10 个媒体")

    media_spec = []
    fields = [("chat_id", str(chat_id))]
    for i, (filename, blob, mime) in enumerate(items):
        attach = f"pic{i}"
        media_spec.append({"type": "photo", "media": f"attach://{attach}"})
        fields.append((attach, (filename, blob, mime)))
    fields.insert(1, ("media", json.dumps(media_spec)))
    return send_multipart("sendMediaGroup", fields)


def send_welfare_videos_by_upload(chat_id, videos_meta):
    """在 Lambda 下载 Drive 文件后以 multipart 上传，避免 Telegram 远端拉 URL 失败。"""
    items = []
    for idx, v in enumerate(videos_meta, start=1):
        fid = v.get("id")
        raw_name = v.get("name")
        filename = _safe_video_filename(raw_name, fid)
        mime = _guess_video_mime(filename)
        print(f"Drive download start: index={idx} file_id={fid} name={filename}")
        blob, err = fetch_drive_video_bytes(fid)
        if err:
            print(f"Drive download failed: file_id={fid} err={err}")
            continue
        if len(blob) > 48 * 1024 * 1024:
            print(f"Skip oversized file: file_id={fid} bytes={len(blob)}")
            continue
        print(f"Drive download ok: file_id={fid} bytes={len(blob)} mime={mime}")
        items.append((filename, blob, mime))

    if not items:
        return False

    i = 0
    while i < len(items):
        remaining = len(items) - i
        if remaining == 1:
            send_video_multipart(chat_id, *items[i])
            i += 1
            continue
        take = min(10, remaining)
        chunk = items[i : i + take]
        send_media_group_videos_multipart(chat_id, chunk)
        i += take
    return True


# Telegram 单张 photo 上限 10MB（官方文档）
_MAX_TELEGRAM_PHOTO_BYTES = 10 * 1024 * 1024


def send_schedule_photos_by_upload(chat_id, images_meta):
    """课表图片：Lambda 下载后以 sendPhoto / sendMediaGroup 上传。"""
    items = []
    for idx, v in enumerate(images_meta, start=1):
        fid = v.get("id")
        raw_name = v.get("name")
        filename = _safe_image_filename(raw_name, fid)
        mime = _guess_image_mime(filename)
        print(f"Schedule photo download start: index={idx} file_id={fid} name={filename}")
        blob, err = fetch_drive_file_bytes(fid)
        if err:
            print(f"Schedule photo download failed: file_id={fid} err={err}")
            continue
        if len(blob) > _MAX_TELEGRAM_PHOTO_BYTES:
            print(
                f"Skip photo over Telegram limit: file_id={fid} bytes={len(blob)} "
                f"(max {_MAX_TELEGRAM_PHOTO_BYTES})"
            )
            continue
        print(f"Schedule photo download ok: file_id={fid} bytes={len(blob)} mime={mime}")
        items.append((filename, blob, mime))

    if not items:
        return False

    i = 0
    while i < len(items):
        remaining = len(items) - i
        if remaining == 1:
            send_photo_multipart(chat_id, *items[i])
            i += 1
            continue
        take = min(10, remaining)
        chunk = items[i : i + take]
        send_media_group_photos_multipart(chat_id, chunk)
        i += take
    return True


def send_request(method, payload):
    url = f"{BASE_URL}/{method}"
    response = http.request(
        "POST", 
        url, 
        body=json.dumps(payload), 
        headers={'Content-Type': 'application/json'}
    )
    # 打印 Telegram API 的返回结果，方便看发送成功没
    print(f"Telegram API Response ({method}): {response.data.decode('utf-8')}")
    return response

def lambda_handler(event, context):
    # 【关键：打印原始事件】这行能让你在 CloudWatch 看到 Telegram 传来的所有内容
    print("--- Incoming Raw Event ---")
    print(json.dumps(event))
    
    try:
        if 'body' not in event:
            return {"statusCode": 400, "body": "no body"}

        data = json.loads(event['body'])
        
        # 处理普通消息
        if "message" in data:
            chat_id = data['message']['chat']['id']
            text = data['message'].get('text', '')
            user = data['message'].get('from', {})
            
            # 在 Log 中清晰记录用户是谁
            print(f"USER_INFO: ID={user.get('id')}, Name={user.get('first_name')}, Username=@{user.get('username')}, Text={text}")

            if text == "/start":
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {"text": "福利视频", "callback_data": "video_gift"},
                            {"text": "课表介绍", "callback_data": "schedule_info"}
                        ],
                        [
                            {"text": "查看日历", "web_app": {"url": "https://sigmaxy.github.io/wanlinbbtg/"}},
                            {"text": "支付定金", "callback_data": "pay_deposit"}
                        ],
                        [
                            {"text": "登记信息", "web_app": {"url": "https://forms.gle/vVjjQshuxwhA16M79"}},
                            {"text": "地址导航", "callback_data": "address_nav"}
                        ],
                        [
                            {"text": "双相解禁", "callback_data": "unlock_request"},
                            {"text": "加入讨论", "url": "https://t.me/linbbdisccussgroup"}
                        ]
                    ]
                }
                send_request("sendMessage", {
                    "chat_id": chat_id,
                    "text": "🌟 欢迎光临！请选择下方服务：",
                    "reply_markup": reply_markup
                })

        # 处理按钮点击
        elif "callback_query" in data:
            cq = data["callback_query"]
            callback_data = cq["data"]
            chat_id = cq["message"]["chat"]["id"]
            user = cq["from"]
            cq_id = cq["id"]

            print(f"CALLBACK_INFO: User=@{user.get('username')}, Clicked={callback_data}")

            send_request("answerCallbackQuery", {"callback_query_id": cq_id})

            if callback_data == "video_gift":
                videos = get_welfare_video_list()
                drive_key = _google_drive_api_key()
                print(
                    f"video_gift routing: has_drive_key={bool(drive_key)} "
                    f"videos={len(videos)}"
                )
                if not videos:
                    send_request(
                        "sendMessage",
                        {
                            "chat_id": chat_id,
                            "text": (
                                "暂时无法自动抓取云端视频，请直接开启文件夹观看：\n"
                                + WELFARE_FOLDER_URL
                                + "\n\n（请在 Lambda 设置 GOOGLE_DRIVE_API_KEY，"
                                "或设置 DRIVE_VIDEO_FILE_IDS=文件ID1,文件ID2）"
                            ),
                        },
                    )
                elif drive_key:
                    print("WELFARE_MODE=upload (Lambda 下载后 multipart 上传)")
                    if context is not None and hasattr(context, "get_remaining_time_in_ms"):
                        remaining_ms = context.get_remaining_time_in_ms()
                        print(f"Lambda remaining time before welfare upload: {remaining_ms}ms")
                        if remaining_ms is not None and remaining_ms < 25000:
                            print(
                                "WARN: Lambda timeout likely too low for download+upload. "
                                "请设 Timeout >= 30 秒（建议 60 秒）。"
                            )
                    sent = send_welfare_videos_by_upload(chat_id, videos)
                    if not sent:
                        send_request(
                            "sendMessage",
                            {
                                "chat_id": chat_id,
                                "text": (
                                    "视频下载失败，请稍后再试或直接开启文件夹：\n"
                                    + WELFARE_FOLDER_URL
                                ),
                            },
                        )
                else:
                    print(
                        "WELFARE_MODE=url_fallback（未设置 GOOGLE_DRIVE_API_KEY，"
                        "Telegram 直接抓 URL，易 MEDIA_INVALID）"
                    )
                    media = build_welfare_video_media_entries(videos)
                    if not media:
                        send_request(
                            "sendMessage",
                            {
                                "chat_id": chat_id,
                                "text": (
                                    "暂时无法自动抓取云端视频，请直接开启文件夹观看：\n"
                                    + WELFARE_FOLDER_URL
                                    + "\n\n（请在 Lambda 设置 GOOGLE_DRIVE_API_KEY，"
                                    "或设置 DRIVE_VIDEO_FILE_IDS=文件ID1,文件ID2）"
                                ),
                            },
                        )
                    else:
                        if context is not None and hasattr(context, "get_remaining_time_in_ms"):
                            remaining_ms = context.get_remaining_time_in_ms()
                            print(f"Lambda remaining time before sendMediaGroup (URL): {remaining_ms}ms")
                            if remaining_ms is not None and remaining_ms < 25000:
                                print(
                                    "WARN: Lambda timeout likely too low for remote video URLs. "
                                    "请在 Lambda 设置 Timeout >= 30 秒（建议 60 秒）。"
                                )
                        for i in range(0, len(media), 10):
                            chunk = media[i : i + 10]
                            print(
                                f"Sending media chunk (URL): start_index={i + 1} "
                                f"chunk_size={len(chunk)} chat_id={chat_id}"
                            )
                            send_request(
                                "sendMediaGroup",
                                {"chat_id": chat_id, "media": chunk},
                            )

            elif callback_data == "schedule_info":
                images = get_schedule_image_list()
                drive_key = _google_drive_api_key()
                print(
                    f"schedule_info routing: has_drive_key={bool(drive_key)} "
                    f"images={len(images)}"
                )
                if not images:
                    send_request(
                        "sendMessage",
                        {
                            "chat_id": chat_id,
                            "text": (
                                "暂时无法读取课表图片，请开启文件夹查看：\n"
                                + SCHEDULE_FOLDER_URL
                                + "\n\n（请确认文件夹已「知道链接者可查看」，"
                                "Lambda 已设置 GOOGLE_DRIVE_API_KEY；"
                                "或设置 DRIVE_SCHEDULE_PHOTO_FILE_IDS）"
                            ),
                        },
                    )
                elif drive_key:
                    print("SCHEDULE_MODE=upload")
                    if context is not None and hasattr(context, "get_remaining_time_in_ms"):
                        remaining_ms = context.get_remaining_time_in_ms()
                        print(f"Lambda remaining time before schedule upload: {remaining_ms}ms")
                        if remaining_ms is not None and remaining_ms < 25000:
                            print(
                                "WARN: Lambda timeout likely too low for schedule photos. "
                                "请设 Timeout >= 30 秒（建议 60 秒）。"
                            )
                    sent = send_schedule_photos_by_upload(chat_id, images)
                    if not sent:
                        send_request(
                            "sendMessage",
                            {
                                "chat_id": chat_id,
                                "text": (
                                    "课表图片下载失败，请稍后再试或直接开启：\n"
                                    + SCHEDULE_FOLDER_URL
                                ),
                            },
                        )
                else:
                    send_request(
                        "sendMessage",
                        {
                            "chat_id": chat_id,
                            "text": (
                                "无法传送课表图片：请在 Lambda 设置 GOOGLE_DRIVE_API_KEY。\n"
                                + SCHEDULE_FOLDER_URL
                            ),
                        },
                    )

            elif callback_data == "pay_deposit":
                send_request("sendMessage", {"chat_id": chat_id, "text": "准备支付宝口令红包，然后填入登记信息"})

            elif callback_data == "address_nav":
                images = get_address_nav_image_list()
                drive_key = _google_drive_api_key()
                print(
                    f"address_nav routing: has_drive_key={bool(drive_key)} "
                    f"images={len(images)}"
                )
                if not drive_key:
                    send_request(
                        "sendMessage",
                        {
                            "chat_id": chat_id,
                            "text": (
                                "无法读取地址导航：请在 Lambda 设置 GOOGLE_DRIVE_API_KEY。\n\n"
                                f"图片文件夹：\n{ADDRESS_NAV_FOLDER_URL}\n\n"
                                f"文字说明：\n{ADDRESS_NAV_TEXT_FILE_URL}"
                            ),
                        },
                    )
                else:
                    if context is not None and hasattr(context, "get_remaining_time_in_ms"):
                        remaining_ms = context.get_remaining_time_in_ms()
                        print(f"Lambda remaining time before address_nav: {remaining_ms}ms")
                        if remaining_ms is not None and remaining_ms < 25000:
                            print(
                                "WARN: Lambda timeout likely too low for address nav "
                                "(文字 + 多图). 请设 Timeout >= 30 秒（建议 60 秒）。"
                            )
                    # 顺序：先图片，再 txt 文字（与需求描述一致）
                    if images:
                        print("ADDRESS_NAV_MODE=upload (photos)")
                        photos_ok = send_schedule_photos_by_upload(chat_id, images)
                        if not photos_ok:
                            send_request(
                                "sendMessage",
                                {
                                    "chat_id": chat_id,
                                    "text": (
                                        "导航图片下载失败，请开启文件夹：\n"
                                        + ADDRESS_NAV_FOLDER_URL
                                    ),
                                },
                            )
                    else:
                        send_request(
                            "sendMessage",
                            {
                                "chat_id": chat_id,
                                "text": (
                                    "文件夹内目前没有可识别的图片，请查看：\n"
                                    + ADDRESS_NAV_FOLDER_URL
                                ),
                            },
                        )
                    nav_text = fetch_address_nav_text()
                    if nav_text is not None and nav_text.strip():
                        print("ADDRESS_NAV: sending text from Drive txt")
                        for part in _split_telegram_messages(nav_text.strip()):
                            send_request(
                                "sendMessage",
                                {"chat_id": chat_id, "text": part},
                            )
                    elif nav_text is None:
                        send_request(
                            "sendMessage",
                            {
                                "chat_id": chat_id,
                                "text": (
                                    "无法下载导航文字档，请直接查看：\n"
                                    + ADDRESS_NAV_TEXT_FILE_URL
                                ),
                            },
                        )

            elif callback_data == "unlock_request":
                send_request("sendMessage", {"chat_id": chat_id, "text": "老师上线之后会跟你联系，耐心等待"})
                # 通知管理员 (请在 Log 中找到 @qubegirlbb 的数字 ID 后填入)
                ADMIN_ID = "123456789" 
                send_request("sendMessage", {"chat_id": ADMIN_ID, "text": f"🔔 双相解禁请求: @{user.get('username', '无用户名')}"})

        return {"statusCode": 200, "body": "ok"}
    
    except Exception as e:
        print(f"ERROR: {str(e)}")
        return {"statusCode": 200}