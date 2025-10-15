import os
import re
import json
import zipfile
import requests
from datetime import datetime, timedelta, timezone
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- 可配置参数 ---
# 【调试模式】: 在此处填入一个已知的视频ID来跳过搜索，直接测试下载和解压功能。
# 测试完成后，请将其改回 None。
DEBUG_VIDEO_ID = "UEBqEeUjOF0" # 例如："ABCDEFG1234"

# YouTube 频道 Handle
CHANNEL_HANDLE = "@fxrj"
# 视频标题中必须包含的关键字
TITLE_KEYWORD = "每日更新"
# 视频简介中匹配网盘链接的正则表达式
DRIVE_URL_PATTERN = r"(https://drive\.google\.com/file/d/[a-zA-Z0-9_-]+)"
# 输出文件夹名称
OUTPUT_DIR = "Nodes"
# 输出文件名
OUTPUT_TXT_FILENAME = "fxrj.txt"
OUTPUT_YAML_FILENAME = "fxrj.yaml"

def get_channel_id(youtube, channel_handle):
    """通过频道 handle (例如 @fxrj) 获取频道 ID"""
    print(f"正在通过 handle '{channel_handle}' 搜索频道 ID...")
    try:
        # 第一次尝试：使用完整的 handle
        print(f"DEBUG: 正在执行 API 搜索, 查询参数 q='{channel_handle}'")
        search_response = youtube.search().list(
            q=channel_handle,
            part="id,snippet",
            type="channel",
            maxResults=1
        ).execute()
        
        # 如果第一次搜索没有结果，尝试去掉'@'进行备用搜索
        if not search_response.get("items"):
            print(f"警告：使用 '{channel_handle}' 未找到频道，尝试不带 '@' 进行搜索...")
            handle_without_at = channel_handle.lstrip('@')
            print(f"DEBUG: 正在执行备用 API 搜索, 查询参数 q='{handle_without_at}'")
            search_response = youtube.search().list(
                q=handle_without_at,
                part="id,snippet",
                type="channel",
                maxResults=1
            ).execute()

        # 打印从 YouTube API 返回的最终响应，这是最重要的调试信息
        print("\n--- DEBUG: 从 YouTube API 返回的原始响应 ---")
        print(json.dumps(search_response, indent=2, ensure_ascii=False))
        print("--- END DEBUG ---\n")

        if "items" in search_response and search_response["items"]:
            # 确认返回的是频道
            item = search_response["items"][0]
            if item["id"]["kind"] == "youtube#channel":
                channel_id = item["id"]["channelId"]
                found_title = item["snippet"]["title"]
                print(f"成功找到频道 '{found_title}'，ID为: {channel_id}")
                return channel_id
        
        print(f"错误：API 返回的结果中不包含预期的频道信息。请检查上面的原始响应并核对您的API密钥设置。")
        return None

    except HttpError as e:
        print(f"获取频道ID时发生 HTTP 错误: {e.reason}")
        if e.resp.status in [403, 400]:
            error_details = json.loads(e.content.decode('utf-8'))
            print("--- API 错误详情 ---")
            print(json.dumps(error_details, indent=2, ensure_ascii=False))
            print("--- END ERROR ---")
            print("\n错误解读: 403/400 错误通常意味着 YouTube API 配额已用尽、API 密钥无效或受限。")
        return None
    except Exception as e:
        print(f"获取频道ID时发生意外错误: {e}")
        return None

def find_target_video(youtube, channel_id):
    """查找指定频道今天发布的、标题包含关键字的最新视频"""
    if not channel_id:
        return None
    tz_beijing = timezone(timedelta(hours=8))
    today_beijing = datetime.now(tz_beijing).date()
    start_of_day_beijing = datetime(today_beijing.year, today_beijing.month, today_beijing.day, tzinfo=tz_beijing)
    end_of_day_beijing = start_of_day_beijing + timedelta(days=1)
    published_after = start_of_day_beijing.astimezone(timezone.utc).isoformat()
    published_before = end_of_day_beijing.astimezone(timezone.utc).isoformat()
    print(f"开始在北京时间 {today_beijing} 查找视频...")
    try:
        search_response = youtube.search().list(
            channelId=channel_id,
            part="id,snippet",
            order="date",
            type="video",
            publishedAfter=published_after,
            publishedBefore=published_before,
            maxResults=50
        ).execute()
        for item in search_response.get("items", []):
            video_title = item["snippet"]["title"]
            if TITLE_KEYWORD in video_title:
                video_id = item["id"]["videoId"]
                print(f"找到目标视频: '{video_title}' (ID: {video_id})")
                return video_id
        print("没有找到今天发布的匹配视频。")
        return None
    except Exception as e:
        print(f"查找视频时出错: {e}")
        return None

def extract_drive_url(youtube, video_id):
    """获取视频简介并提取谷歌网盘链接"""
    if not video_id:
        return None
    try:
        video_response = youtube.videos().list(id=video_id, part="snippet").execute()
        description = video_response["items"][0]["snippet"]["description"]
        match = re.search(DRIVE_URL_PATTERN, description)
        if match:
            url = match.group(1)
            print(f"在简介中找到谷歌网盘链接: {url}")
            return url
        else:
            print("简介中未找到谷歌网盘链接。")
            return None
    except Exception as e:
        print(f"提取链接时出错: {e}")
        return None

def download_and_extract(url):
    """从谷歌网盘下载文件并解压到指定目录 (使用 requests 库)"""
    if not url:
        return False
    
    temp_zip_path = "temp.zip"
    try:
        print(f"开始从 {url} 下载文件...")
        
        # 从 URL 中提取文件 ID
        file_id = url.split('/d/')[-1].split('/')[0]
        download_url = f'https://docs.google.com/uc?export=download&id={file_id}'
        
        # 使用 session 来处理 cookies
        session = requests.Session()
        response = session.get(download_url, stream=True)
        
        # 检查是否需要确认下载（针对大文件）
        token = None
        for key, value in response.cookies.items():
            if key.startswith('download_warning'):
                token = value
        
        # 如果需要确认，带上 token 再次请求
        if token:
            print("需要确认下载，正在带 token 重新请求...")
            params = {'id': file_id, 'export': 'download', 'confirm': token}
            response = session.get('https://docs.google.com/uc', params=params, stream=True)

        # 将下载内容写入文件
        with open(temp_zip_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=32768):
                if chunk:
                    f.write(chunk)
        
        print("文件下载完成，开始解压...")
        
        if not os.path.exists(OUTPUT_DIR):
            os.makedirs(OUTPUT_DIR)
            print(f"已创建输出文件夹: {OUTPUT_DIR}")

        found_files = False
        with zipfile.ZipFile(temp_zip_path, 'r') as zip_ref:
            for file_info in zip_ref.infolist():
                # 跳过 macOS 系统的元数据文件夹
                if file_info.filename.startswith('__MACOSX/'):
                    continue
                if file_info.filename.endswith('.txt'):
                    zip_ref.extract(file_info, OUTPUT_DIR)
                    original_path = os.path.join(OUTPUT_DIR, file_info.filename)
                    new_path = os.path.join(OUTPUT_DIR, OUTPUT_TXT_FILENAME)
                    os.rename(original_path, new_path)
                    print(f"已解压并重命名文件: {OUTPUT_TXT_FILENAME}")
                    found_files = True
                elif file_info.filename.endswith('.yaml'):
                    zip_ref.extract(file_info, OUTPUT_DIR)
                    original_path = os.path.join(OUTPUT_DIR, file_info.filename)
                    new_path = os.path.join(OUTPUT_DIR, OUTPUT_YAML_FILENAME)
                    os.rename(original_path, new_path)
                    print(f"已解压并重命名文件: {OUTPUT_YAML_FILENAME}")
                    found_files = True

        if not found_files:
            print("警告：压缩包中未找到 .txt 或 .yaml 文件。")

        return True # 只要下载成功就返回 True
    except zipfile.BadZipFile:
        print(f"下载或解压文件时出错: 文件不是一个有效的zip压缩包。")
        print(f"错误发生，已将下载的文件保留为 '{temp_zip_path}'，请手动检查。")
        return False
    except Exception as e:
        print(f"下载或解压文件时出错: {e}")
        if os.path.exists(temp_zip_path):
             print(f"错误发生，已将下载的文件保留为 '{temp_zip_path}'，请手动检查。")
        return False
    finally:
        # 仅在一切成功且找到文件后才删除
        if 'found_files' in locals() and found_files and os.path.exists(temp_zip_path):
            os.remove(temp_zip_path)


def main():
    """主执行函数"""
    print("--- 开始运行每日节点更新脚本 ---")
    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        print("错误: 未找到环境变量 YOUTUBE_API_KEY。")
        return

    try:
        youtube = build("youtube", "v3", developerKey=api_key)
        
        video_id = None
        if DEBUG_VIDEO_ID and DEBUG_VIDEO_ID not in ["请在这里填入一个用于测试的视频ID", ""]:
            print(f"--- 调试模式已开启：正在使用指定的视频ID: {DEBUG_VIDEO_ID} ---")
            video_id = DEBUG_VIDEO_ID
        else:
            channel_id = get_channel_id(youtube, CHANNEL_HANDLE)
            if not channel_id:
                print(f"错误：找不到频道ID {CHANNEL_HANDLE}")
                print("--- 脚本结束：未能获取频道ID。 ---")
                return
            video_id = find_target_video(youtube, channel_id)
        
        if not video_id:
            print("--- 脚本结束：未找到匹配的视频。 ---")
            return
            
        drive_url = extract_drive_url(youtube, video_id)
        if not drive_url:
            print("--- 脚本结束：未找到下载链接。 ---")
            return

        success = download_and_extract(drive_url)
        if success:
            print("--- 脚本成功执行。 ---")
        else:
            print("--- 脚本执行完成，但出现问题。 ---")

    except Exception as e:
        print(f"发生意外错误: {e}")

if __name__ == "__main__":
    main()

