import os
import re
import threading

from common import download_segment, manager, parse_episodes
from utils.common import Task, repair_filename, touch_dir
from utils.crawler import BililiCrawler
from utils.ffmpeg import FFmpeg
from utils.playlist import Dpl, M3u
from utils.thread import ThreadPool

info_api = "https://api.bilibili.com/pgc/web/season/section?season_id={season_id}"
parse_api = "https://api.bilibili.com/pgc/player/web/playurl?avid={avid}&cid={cid}&qn={sp}&ep_id={ep_id}"
spider = BililiCrawler()
GLOBAL = dict()


def get_title(url):
    """ 获取视频标题 """
    res = spider.get(url)
    title = re.search(
        r'<span class="media-info-title-t">(.*?)</span>', res.text).group(1)
    return title


def get_info(url):
    """ 从 url 中获取视频所需信息 """
    info = []
    season_id = re.match(
        r'https?://www.bilibili.com/bangumi/media/md(\d+)', url).group(1)

    info_url = info_api.format(season_id=season_id)
    res = spider.get(info_url)

    for i, item in enumerate(res.json()["result"]["main_section"]["episodes"]):
        index = item["title"]
        if re.match(r'^\d*\.?\d*$', index):
            index = '第{}话'.format(index)
        name = repair_filename(' '.join([index, item["long_title"]]))
        file_path = os.path.join(GLOBAL['video_dir'], repair_filename(
            '{}.mp4'.format(name)))
        if GLOBAL['playlist'] is not None:
            GLOBAL['playlist'].write_path(file_path)
        info.append({
            "num": i+1,
            "aid": item["aid"],
            "cid": item["cid"],
            "id": item["id"],
            "name": name,
            "file_path": file_path,
            "merged": False,
            "segments": []
        })

    return info


def parse_segment_info(aid, cid, ep_id):
    """ 解析视频片段 url """

    segments = []

    # 搜索支持的清晰度，并匹配最佳清晰度
    accept_quality = spider.get(parse_api.format(avid=aid, cid=cid, ep_id=ep_id, sp=80)).json()[
        'result']['accept_quality']
    for sp in GLOBAL['sp_seq']:
        if sp in accept_quality:
            break

    parse_url = parse_api.format(avid=aid, cid=cid, ep_id=ep_id, sp=sp)
    res = spider.get(parse_url)

    for i, segment in enumerate(res.json()['result']['durl']):
        segments.append({
            "num": i+1,
            "url": segment["url"],
            "sp": sp,
            "file_path": None,
            "downloaded": False
        })
    return segments


def start(url, config):
    # 获取标题
    GLOBAL.update(config)
    GLOBAL["spider"] = spider
    GLOBAL["ffmpeg"] = FFmpeg(GLOBAL["ffmpeg_path"])
    title = get_title(url)
    print(title)

    # 创建所需目录结构
    GLOBAL["base_dir"] = touch_dir(os.path.join(
        GLOBAL['dir'], title + " - bilibili"))
    GLOBAL["video_dir"] = touch_dir(os.path.join(GLOBAL['base_dir'], "Videos"))
    if GLOBAL["playlist_type"] == "dpl":
        GLOBAL['playlist'] = Dpl(os.path.join(
            GLOBAL['base_dir'], 'Playlist.dpl'), path_type=GLOBAL["playlist_path_type"])
    elif GLOBAL["playlist_type"] == "m3u":
        GLOBAL['playlist'] = M3u(os.path.join(
            GLOBAL['base_dir'], 'Playlist.m3u'), path_type=GLOBAL["playlist_path_type"])
    else:
        GLOBAL['playlist'] = None

    # 获取需要的信息
    info = get_info(url)
    GLOBAL["info"] = info
    if GLOBAL['playlist'] is not None:
        GLOBAL['playlist'].flush()

    # 解析并过滤需要的选集
    episodes = parse_episodes(GLOBAL["episodes"], len(info))
    info = list(filter(lambda item: item["num"] in episodes, info))
    GLOBAL["info"] = info

    # 解析片段信息及视频 url
    for i, item in enumerate(info):
        print("{:02}/{:02} parsing segments info...".format(i, len(info)), end="\r")
        item["segments"] = parse_segment_info(
            item["aid"], item["cid"], item["id"])

    # 创建下载线程池，准备下载
    pool = ThreadPool(GLOBAL["num_thread"])

    # 为线程池添加下载任务
    for item in info:
        for segment in item["segments"]:
            segment["file_path"] = os.path.join(GLOBAL['video_dir'], repair_filename(
                '{}_{:02d}.flv'.format(item["name"], segment["num"])))
            pool.add_task(Task(download_segment, (segment, item, GLOBAL)))

    # 启动下载线程池
    pool.run()

    # 创建并启动监控线程
    manager_thread = threading.Thread(target=manager, args=(GLOBAL, ))
    manager_thread.setDaemon(True)
    manager_thread.start()

    # 等待下载全部完成
    pool.join()

    # 等待合并全部完成
    manager_thread.join()
