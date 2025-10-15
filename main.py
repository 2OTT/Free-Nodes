import os
import re
import shutil
import zipfile
from datetime import datetime, timedelta, timezone

import gdown
from dateutil import parser
from googleapiclient.discovery import build

# --- 配置信息 ---
# 目标YouTube频道的ID (@fxrj)
CHANNEL_ID = 'UCCp_42s_y_2n9u_t-2ciP-A'
# 视频标题中必须包含的关键字
TITLE_KEYWORD = '每日更新'
# 视频简介中要匹配的谷歌网盘链接格式
DRIVE_URL_PATTERN = r'https?://drive\.google\.com/file/d/([a-zA-Z0-9_-]+)'
# 最终输出文件的存放目录
OUTPUT_DIR = 'Nodes'
# 下载和解压的临时目录
TEMP_DIR = 'temp_download'


def get_youtube_service():
    """初始化并返回 YouTube Data API 服务客户端"""
    api_key = os.getenv('YOUTUBE_API_KEY')
    if not api_key:
        raise ValueError("错误：未设置 YOUTUBE_API_KEY 环境变量。")
    return build('youtube', 'v3', developerKey=api_key)


def find_target_video(service):
    """
    在指定频道中查找“今天”（北京时间）发布且标题包含关键字的最新视频。
    """
    # 定义北京时区 (UTC+8)
    beijing_tz = timezone(timedelta(hours=8))
    today_beijing = datetime.now(beijing_tz).date()

    print(f"开始在北京时间 {today_beijing} 查找视频...")

    # 通过频道ID获取其上传列表的ID
    request = service.channels().list(part='contentDetails', id=CHANNEL_ID)
    response = request.execute()
    if not response.get('items'):
        print(f"错误：找不到频道ID {CHANNEL_ID}")
        return None, None

    uploads_playlist_id = response['items'][0]['contentDetails']['relatedPlaylists']['uploads']

    # 获取上传列表中的最新视频
    request = service.playlistItems().list(
        part='snippet,contentDetails',
        playlistId=uploads_playlist_id,
        maxResults=10  # 检查最近的10个视频，对于每日更新足够了
    )
    playlist_response = request.execute()

    for item in playlist_response.get('items', []):
        snippet = item['snippet']
        video_title = snippet['title']
        publish_time_str = snippet['publishedAt']

        # 将UTC发布时间转换为北京时间
        publish_time_utc = parser.isoparse(publish_time_str)
        publish_time_beijing = publish_time_utc.astimezone(beijing_tz)

        print(f"检查视频: '{video_title}' (发布于 {publish_time_beijing.date()})")

        # 检查视频是否是今天（北京时间）发布的，并且标题包含关键字
        if publish_time_beijing.date() == today_beijing and TITLE_KEYWORD in video_title:
            print(f"找到目标视频: '{video_title}'")
            video_id = item['contentDetails']['videoId']
            # 需要再次请求视频详情以获取完整的简介
            video_request = service.videos().list(part='snippet', id=video_id)
            video_response = video_request.execute()
            video_description = video_response['items'][0]['snippet']['description']
            return video_title, video_description

    print("没有找到今天发布的匹配视频。")
    return None, None


def extract_drive_url(description):
    """从视频简介中提取谷歌网盘链接"""
    if not description:
        return None
    match = re.search(DRIVE_URL_PATTERN, description)
    if match:
        file_id = match.group(1)
        # 构造成 gdown 可以直接下载的链接格式
        url = f'https://drive.google.com/uc?id={file_id}'
        print(f"在简介中找到谷歌网盘链接: {url}")
        return url
    print("简介中未找到谷歌网盘链接。")
    return None


def download_and_extract(url):
    """从URL下载文件，解压，并清理临时文件"""
    if os.path.exists(TEMP_DIR):
        shutil.rmtree(TEMP_DIR)
    os.makedirs(TEMP_DIR)

    download_path = os.path.join(TEMP_DIR, 'downloaded_archive.zip')

    try:
        print(f"开始从 {url} 下载文件...")
        gdown.download(url, download_path, quiet=False)
        print("文件下载完成。")
    except Exception as e:
        print(f"下载文件失败: {e}")
        return False

    if not os.path.exists(download_path) or os.path.getsize(download_path) == 0:
        print("下载的文件不存在或是空的。")
        return False

    # 确保输出目录存在
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    try:
        print(f"开始解压文件: {download_path}")
        with zipfile.ZipFile(download_path, 'r') as zip_ref:
            found_txt = False
            found_yaml = False
            for member in zip_ref.namelist():
                # 查找并处理 .txt 文件
                if member.endswith('.txt') and not found_txt:
                    source = zip_ref.open(member)
                    target_path = os.path.join(OUTPUT_DIR, 'fxrj.txt')
                    with open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)
                    print(f"已解压 '{member}' 并重命名为 '{target_path}'")
                    found_txt = True

                # 查找并处理 .yaml 文件
                if member.endswith('.yaml') and not found_yaml:
                    source = zip_ref.open(member)
                    target_path = os.path.join(OUTPUT_DIR, 'fxrj.yaml')
                    with open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)
                    print(f"已解压 '{member}' 并重命名为 '{target_path}'")
                    found_yaml = True
                
                if found_txt and found_yaml:
                    break  # 找到两种文件后即可停止

        if not found_txt and not found_yaml:
             print("压缩包中没有找到 .txt 或 .yaml 文件。")
             return False

        return True
    except zipfile.BadZipFile:
        print("错误：下载的文件不是一个有效的zip压缩包。")
        return False
    except Exception as e:
        print(f"解压过程中发生错误: {e}")
        return False
    finally:
        # 清理临时目录
        shutil.rmtree(TEMP_DIR)
        print(f"已清理临时目录: {TEMP_DIR}")


def main():
    """主执行函数"""
    print("--- 开始运行每日节点更新脚本 ---")
    try:
        youtube = get_youtube_service()
        _, video_description = find_target_video(youtube)
        if video_description:
            drive_url = extract_drive_url(video_description)
            if drive_url:
                success = download_and_extract(drive_url)
                if success:
                    print("--- 脚本成功完成！ ---")
                else:
                    print("--- 脚本在下载或解压时出错。 ---")
            else:
                print("--- 脚本结束：没有需要处理的URL。 ---")
        else:
            print("--- 脚本结束：未找到匹配的视频。 ---")
    except Exception as e:
        print(f"发生意外错误: {e}")


if __name__ == '__main__':
    main()
