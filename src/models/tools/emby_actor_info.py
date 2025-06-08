"""
补全emby信息及头像
"""

import os
import random
import re
import shutil
import time
import traceback
import urllib

import bs4
import langid
import zhconv
from lxml import etree

from models.base.file import copy_file
from models.base.utils import get_used_time
from models.base.web import get_html, post_html
from models.config.config import config
from models.config.resources import resources
from models.core.flags import Flags
from models.core.translate import deepl_translate, youdao_translate,ollama_translate
from models.core.utils import get_movie_path_setting
from models.core.web import download_file_with_filepath, google_translate
from models.data_models import EMbyActressInfo
from models.signals import signal
from models.tools.actress_db import ActressDB
from models.tools.emby_actor_image import (
    _generate_server_url,
    _get_emby_actor_list,
    _get_gfriends_actor_data,
    update_emby_actor_photo,
)


def creat_kodi_actors(add: bool):
    signal.change_buttons_status.emit()
    signal.show_log_text(f"📂 待刮削目录: {get_movie_path_setting()[0]}")
    if add:
        signal.show_log_text("💡 将为待刮削目录中的每个视频创建 .actors 文件夹，并补全演员图片到 .actors 文件夹中\n")
        signal.show_log_text("👩🏻 开始补全 Kodi/Plex/Jvedio 演员头像...")
        gfriends_actor_data = _get_gfriends_actor_data()
    else:
        signal.show_log_text("💡 将清除该目录下的所有 .actors 文件夹...\n")
        gfriends_actor_data = True

    if gfriends_actor_data:
        _deal_kodi_actors(gfriends_actor_data, add)
    signal.reset_buttons_status.emit()
    signal.show_log_text("================================================================================")


def update_emby_actor_info():
    signal.change_buttons_status.emit()
    start_time = time.time()
    emby_on = config.emby_on
    server_name = "Emby" if "emby" in config.server_type else "Jellyfin"
    signal.show_log_text(f"👩🏻 开始补全 {server_name} 演员信息...")

    actor_list = _get_emby_actor_list()
    if actor_list:
        i = 0
        total = len(actor_list)
        wiki = 0
        updated = 0
        for actor in actor_list:
            i += 1
            actor_name = actor.get("Name")
            server_id = actor.get("ServerId")
            actor_id = actor.get("Id")

            # 名字含有空格时跳过
            if re.search(r"[ .·・-]", actor_name):
                signal.show_log_text(f"🔍 {i}/{total} {actor_name}: 名字含有空格等分隔符，识别为非女优，跳过！")
                continue

            # 已有资料时跳过
            # http://192.168.5.191:8096/emby/Persons/梦乃爱华?api_key=ee9a2f2419704257b1dd60b975f2d64e
            actor_homepage, actor_person, pic_url, backdrop_url, backdrop_url_0, update_url = _generate_server_url(
                actor
            )
            result, res = get_html(actor_person, proxies=False, json_data=True)
            if not result:
                signal.show_log_text(
                    f"🔴 {i}/{total} {actor_name}: {server_name} 获取演员信息错误！\n    错误信息: {res}"
                )
                continue
            if res.get("Overview") and "actor_info_miss" in emby_on:
                signal.show_log_text(f"✅ {i}/{total} {actor_name}: {server_name} 已有演员信息！跳过！")
                continue

            # 通过 wiki 及本地数据库获取演员信息
            signal.show_log_text(f"🔍 {i}/{total} 开始请求： {actor_name}\n" + "=" * 80)
            actor_info = EMbyActressInfo(name=actor_name, server_id=server_id, id=actor_id)
            exist = False
            db_exist = False
            try:
                if x := _search_wiki(actor_info):
                    url, url_log = x
                    if _get_wiki_detail(url, url_log, actor_info):
                        exist = True
                        wiki += 1
                if config.use_database:
                    db_exist = ActressDB.update_actor_info_from_db(actor_info)
                if db_exist or exist:
                    r, res = post_html(update_url, json=actor_info.dump(), use_proxy=False)
                    if r:
                        signal.show_log_text(f"\n ✅ 演员信息更新成功！\n 👩🏻 点击查看 {actor_name} 的 Emby 演员主页:")
                        signal.show_log_text(f" {actor_homepage}")
                        updated += 1
                    else:
                        signal.show_log_text(f"\n 🔴 演员信息更新失败！\n    错误信息: {res}")
                else:
                    signal.show_log_text(f"🔴 {i}/{total} {actor_name}: 未检索到演员信息！跳过！")
                    continue
            except:
                signal.show_log_text(traceback.format_exc())
                continue
            signal.show_log_text("=" * 80)
        signal.show_log_text(
            f"\n\n🎉🎉🎉 补全完成！！！ 用时 {get_used_time(start_time)} 秒"
            f" 共更新: {updated} Wiki 获取: {wiki} 仅数据库: {updated - wiki}"
        )

    if "actor_info_photo" in emby_on:
        for i in range(5):
            signal.show_log_text(f"{5 - i} 秒后开始补全演员头像头像...")
            time.sleep(1)
        signal.show_log_text("\n")
        signal.change_buttons_status.emit()
        update_emby_actor_photo()
        signal.reset_buttons_status.emit()
    else:
        signal.reset_buttons_status.emit()


def show_emby_actor_list(mode):
    signal.change_buttons_status.emit()
    start_time = time.time()

    mode += 1
    if mode == 1:
        signal.show_log_text("🚀 开始查询所有演员列表...")
    elif mode == 2:
        signal.show_log_text("🚀 开始查询 有头像，有信息 的演员列表...")
    elif mode == 3:
        signal.show_log_text("🚀 开始查询 没头像，有信息 的演员列表...")
    elif mode == 4:
        signal.show_log_text("🚀 开始查询 有头像，没信息 的演员列表...")
    elif mode == 5:
        signal.show_log_text("🚀 开始查询 没信息，没头像 的演员列表...")
    elif mode == 6:
        signal.show_log_text("🚀 开始查询 有信息 的演员列表...")
    elif mode == 7:
        signal.show_log_text("🚀 开始查询 没信息 的演员列表...")
    elif mode == 8:
        signal.show_log_text("🚀 开始查询 有头像 的演员列表...")
    elif mode == 9:
        signal.show_log_text("🚀 开始查询 没头像 的演员列表...")

    actor_list = _get_emby_actor_list()
    if actor_list:
        count = 1
        succ_pic = 0
        fail_pic = 0
        succ_info = 0
        fail_info = 0
        succ = 0
        fail_noinfo = 0
        fail_nopic = 0
        fail = 0
        total = len(actor_list)
        actor_list_temp = ""
        logs = ""
        for actor_js in actor_list:
            actor_name = actor_js["Name"]
            actor_imagetages = actor_js["ImageTags"]
            actor_homepage, actor_person, pic_url, backdrop_url, backdrop_url_0, update_url = _generate_server_url(
                actor_js
            )
            # http://192.168.5.191:8096/web/index.html#!/item?id=2146&serverId=57cdfb2560294a359d7778e7587cdc98

            if actor_imagetages:
                succ_pic += 1
                actor_list_temp = f"\n✅ {count}/{total} 已有头像！ 👩🏻 {actor_name} \n{actor_homepage}"
            else:
                fail_pic += 1
                actor_list_temp = f"\n🔴 {count}/{total} 没有头像！ 👩🏻 {actor_name} \n{actor_homepage}"

            if mode > 7:
                if mode == 8 and actor_imagetages:
                    actor_list_temp = f"\n✅ {succ_pic}/{total} 已有头像！ 👩🏻 {actor_name} \n{actor_homepage}"
                    logs += actor_list_temp + "\n"
                elif mode == 9 and not actor_imagetages:
                    actor_list_temp = f"\n🔴 {fail_pic}/{total} 没有头像！ 👩🏻 {actor_name} \n{actor_homepage}"
                    logs += actor_list_temp + "\n"
                if count % 100 == 0 or (succ_pic + fail_pic) == total:
                    signal.show_log_text(logs)
                    time.sleep(0.01)
                    logs = ""
                count += 1
            else:
                # http://192.168.5.191:8096/emby/Persons/梦乃爱华?api_key=ee9a2f2419704257b1dd60b975f2d64e
                result, res = get_html(actor_person, proxies=False, json_data=True)
                if not result:
                    signal.show_log_text(
                        f"\n🔴 {count}/{total} Emby 获取演员信息错误！👩🏻 {actor_name} \n    错误信息: {res}"
                    )
                    continue
                overview = res.get("Overview")

                if overview:
                    succ_info += 1
                else:
                    fail_info += 1

                if mode == 1:
                    if actor_imagetages and overview:
                        signal.show_log_text(
                            f"\n✅ {count}/{total} 已有信息！已有头像！ 👩🏻 {actor_name} \n{actor_homepage}"
                        )
                        succ += 1
                    elif actor_imagetages:
                        signal.show_log_text(
                            f"\n🔴 {count}/{total} 没有信息！已有头像！ 👩🏻 {actor_name} \n{actor_homepage}"
                        )
                        fail_noinfo += 1
                    elif overview:
                        signal.show_log_text(
                            f"\n🔴 {count}/{total} 已有信息！没有头像！ 👩🏻 {actor_name} \n{actor_homepage}"
                        )
                        fail_nopic += 1
                    else:
                        signal.show_log_text(
                            f"\n🔴 {count}/{total} 没有信息！没有头像！ 👩🏻 {actor_name} \n{actor_homepage}"
                        )
                        fail += 1
                    count += 1
                elif mode == 2 and actor_imagetages and overview:
                    signal.show_log_text(
                        f"\n✅ {count}/{total} 已有信息！已有头像！ 👩🏻 {actor_name} \n{actor_homepage}"
                    )
                    count += 1
                    succ += 1
                elif mode == 3 and not actor_imagetages and overview:
                    signal.show_log_text(
                        f"\n🔴 {count}/{total} 已有信息！没有头像！ 👩🏻 {actor_name} \n{actor_homepage}"
                    )
                    count += 1
                    fail_nopic += 1
                elif mode == 4 and actor_imagetages and not overview:
                    signal.show_log_text(
                        f"\n🔴 {count}/{total} 没有信息！已有头像！ 👩🏻 {actor_name} \n{actor_homepage}"
                    )
                    count += 1
                    fail_noinfo += 1
                elif mode == 5 and not actor_imagetages and not overview:
                    signal.show_log_text(
                        f"\n🔴 {count}/{total} 没有信息！没有头像！ 👩🏻 {actor_name} \n{actor_homepage}"
                    )
                    count += 1
                    fail += 1
                elif mode == 6 and overview:
                    signal.show_log_text(f"\n✅ {count}/{total} 已有信息！ 👩🏻 {actor_name} \n{actor_homepage}")
                    count += 1
                elif mode == 7 and not overview:
                    signal.show_log_text(f"\n🔴 {count}/{total} 没有信息！ 👩🏻 {actor_name} \n{actor_homepage}")
                    count += 1

        signal.show_log_text(f"\n\n🎉🎉🎉 查询完成！ 用时: {get_used_time(start_time)}秒")
        if mode == 1:
            signal.show_log_text(
                f"👩🏻 演员数量: {total} ✅ 有头像有信息: {succ} 🔴 有头像没信息: {fail_noinfo} 🔴 没头像有信息: {fail_nopic} 🔴 没头像没信息: {fail}\n"
            )
        elif mode == 2:
            other = total - succ
            signal.show_log_text(f"👩🏻 演员数量: {total} ✅ 有头像有信息: {succ} 🔴 其他: {other}\n")
        elif mode == 3:
            signal.show_log_text(f"👩🏻 演员数量: {total} 🔴 有信息没头像: {fail_nopic}\n")
        elif mode == 4:
            signal.show_log_text(f"👩🏻 演员数量: {total} 🔴 有头像没信息: {fail_noinfo}\n")
        elif mode == 5:
            signal.show_log_text(f"👩🏻 演员数量: {total} 🔴 没信息没头像: {fail}\n")
        elif mode == 6 or mode == 7:
            signal.show_log_text(f"👩🏻 演员数量: {total} ✅ 已有信息: {succ_info} 🔴 没有信息: {fail_info}\n")
        else:
            signal.show_log_text(f"👩🏻 演员数量: {total} ✅ 已有头像: {succ_pic} 🔴 没有头像: {fail_pic}\n")
        signal.show_log_text("================================================================================")
        signal.reset_buttons_status.emit()


def _get_wiki_detail(url, url_log, actor_info: EMbyActressInfo):
    ja = True if "ja." in url else False
    emby_on = config.emby_on
    result, res = get_html(url)
    if not result:
        signal.show_log_text(f" 🔴 维基百科演员页请求失败！\n    错误信息: {res}\n    请求地址: {url}")
        return False
    if "noarticletext mw-content-ltr" in res:
        signal.show_log_text(" 🔴 维基百科演员页没有该词条！")
        return False

    av_key = [
        "女优",
        "女優",
        "男优",
        "男優",
        "（AV）导演",
        "AV导演",
        "AV監督",
        "成人电影",
        "成人影片",
        "映画監督",
        "アダルトビデオ監督",
        "电影导演",
        "配音員",
        "配音员",
        "声優",
        "声优",
        "グラビアアイドル",
        "モデル",
    ]
    for key in av_key:
        if key in res:
            signal.show_log_text(f" 🎉 页面内容命中关键词: {key}，识别为女优或写真偶像或导演！\n")
            break
    else:
        signal.show_log_text(" 🔴 页面内容未命中关键词，识别为非女优或导演！")
        return False

    res = re.sub(r"<a href=\"#cite_note.*?</a>", "", res)  # 替换[1],[2]等注释
    soup = bs4.BeautifulSoup(res, "lxml")
    actor_output = soup.find(class_="mw-parser-output")

    # 开头简介
    actor_introduce_0 = actor_output.find(id="mf-section-0")
    begin_intro = actor_introduce_0.find_all(name="p")
    overview = ""
    for each in begin_intro:
        info = each.get_text("", strip=True)
        overview += info + "\n"

    # 个人资料
    actor_info.locations = ["日本"]
    actor_profile = actor_output.find(name="table", class_=["infobox", "infobox vcard plainlist"])
    if actor_profile:
        att_keys = actor_profile.find_all(scope=["row"])
        att_values = actor_profile.find_all(name="td", style=[""], colspan=False)
        bday = actor_output.find(class_="bday")
        bday = f"({bday.get_text('', strip=True)})" if bday else ""
        if att_keys and att_values:
            overview += "\n===== 个人资料 =====\n"
            i = 0
            for each in att_keys:
                info_left = each.text.strip()
                info_right = att_values[i].get_text("", strip=True).replace(bday, "")
                info = info_left + ": " + info_right
                overview += info + "\n"
                if "出生" in info_left or "生年" in info_left:
                    result = re.findall(r"(\d+)年(\d+)月(\d+)日", info_right)
                    if result:
                        result = result[0]
                        year = (
                            str(result[0])
                            if len(result[0]) == 4
                            else "19" + str(result[0])
                            if len(result[0]) == 2
                            else "1970"
                        )
                        month = str(result[1]) if len(result[1]) == 2 else "0" + str(result[1])
                        day = str(result[2]) if len(result[2]) == 2 else "0" + str(result[2])
                        brithday = f"{year}-{month}-{day}"
                        actor_info.birthday = brithday
                        actor_info.year = year
                elif "出身地" in info_left or "出道地点" in info_left:
                    location = re.findall(r"[^ →]+", info_right)
                    if location:
                        location = location[0]
                        if location != "日本":
                            if ja and "actor_info_translate" in emby_on and "actor_info_ja" not in emby_on:
                                location = location.replace("県", "县")
                                if "actor_info_zh_cn" in emby_on:
                                    location = zhconv.convert(location, "zh-cn")
                                elif "actor_info_zh_tw" in emby_on:
                                    location = zhconv.convert(location, "zh-hant")
                            location = "日本·" + location.replace("日本・", "").replace("日本·", "").replace("日本", "")
                        actor_info.locations = [f"{location}"]
                i += 1

    # 人物
    try:
        s = actor_introduce_0.find(class_="toctext", text=["人物"]).find_previous_sibling().string
        if s:
            ff = actor_output.find(id=f"mf-section-{s}")
            if ff:
                actor_1 = ff.find_all(name=["p", "li"])
                overview += "\n===== 人物介绍 =====\n"
                for each in actor_1:
                    info = each.get_text("", strip=True)
                    overview += info + "\n"
    except:
        signal.show_traceback_log(traceback.format_exc())

    # 简历
    try:
        s = (
            actor_introduce_0.find(
                class_="toctext",
                text=[
                    "简历",
                    "簡歷",
                    "个人简历",
                    "個人簡歷",
                    "略歴",
                    "経歴",
                    "来歴",
                    "生平",
                    "生平与职业生涯",
                    "略歴・人物",
                ],
            )
            .find_previous_sibling()
            .string
        )
        if s:
            ff = actor_output.find(id=f"mf-section-{s}")
            if ff:
                overview += "\n===== 个人经历 =====\n"
                actor_1 = ff.find_all(name=["p", "li"])
                for each in actor_1:
                    info = each.get_text("", strip=True)
                    overview += info + "\n"
    except:
        signal.show_traceback_log(traceback.format_exc())

    # 翻译
    try:
        overview_req = ""
        tag_req = ""
        tag_trans = actor_info.taglines_translate
        if (ja or tag_trans) and "actor_info_translate" in emby_on and "actor_info_ja" not in emby_on:
            translate_by_list = Flags.translate_by_list.copy()
            random.shuffle(translate_by_list)
            if ja and overview:
                overview_req = overview
            if tag_trans:
                tag_req = actor_info.taglines[0]

                # 为英文时要单独进行翻译
                if tag_req and langid.classify(tag_req)[0] == "en" and translate_by_list:
                    for each in translate_by_list:
                        signal.show_log_text(
                            f" 🐙 识别到演员描述信息为英文({tag_req})，请求 {each.capitalize()} 进行翻译..."
                        )
                        if each == "youdao":  # 使用有道翻译
                            t, o, r = youdao_translate(tag_req, "")
                        elif each == "google":  # 使用 google 翻译
                            t, o, r = ollama_translate(tag_req, "")
                        else:  # 使用deepl翻译
                            t, o, r = deepl_translate(tag_req, "", ls="EN")
                        if r:
                            signal.show_log_text(f" 🔴 Translation failed!({each.capitalize()}) Error: {r}")
                        else:
                            actor_info.taglines = [t]
                            tag_req = ""
                            break
                    else:
                        signal.show_log_text(f"\n 🔴 Translation failed! {each.capitalize()} 不可用！")

            if (overview_req or tag_req) and translate_by_list:
                for each in translate_by_list:
                    signal.show_log_text(f" 🐙 请求 {each.capitalize()} 翻译演员信息...")
                    if each == "youdao":  # 使用有道翻译
                        t, o, r = youdao_translate(tag_req, overview_req)
                    elif each == "google":  # 使用 google 翻译
                        t, o, r = ollama_translate(tag_req, overview_req)
                    else:  # 使用deepl翻译
                        t, o, r = deepl_translate(tag_req, overview_req)
                    if r:
                        signal.show_log_text(f" 🔴 Translation failed!({each.capitalize()}) Error: {r}")
                    else:
                        if tag_req:
                            actor_info.taglines = [t]
                        if overview_req:
                            overview = o
                            overview = overview.replace("\n= = = = = = = = = =个人资料\n", "\n===== 个人资料 =====\n")
                            overview = overview.replace("\n=====人物介绍\n", "\n===== 人物介绍 =====\n")
                            overview = overview.replace("\n= = = = =个人鉴定= = = = =\n", "\n===== 个人经历 =====\n")
                            overview = overview.replace("\n=====个人日历=====\n", "\n===== 个人经历 =====\n")
                            overview = overview.replace("\n=====个人费用=====\n", "\n===== 个人资料 =====\n")
                            overview = overview.replace("\n===== 个人协助 =====\n", "\n===== 人物介绍 =====\n")
                            overview = overview.replace("\n===== 个人经济学 =====\n", "\n===== 个人经历 =====\n")
                            overview = overview.replace("\n===== 个人信息 =====\n", "\n===== 个人资料 =====\n")
                            overview = overview.replace("\n===== 简介 =====\n", "\n===== 人物介绍 =====\n")
                            overview = overview.replace(":", ": ") + "\n"
                            if "=====\n" not in overview:
                                overview = overview.replace(" ===== 个人资料 ===== ", "\n===== 个人资料 =====\n")
                                overview = overview.replace(" ===== 人物介绍 ===== ", "\n===== 人物介绍 =====\n")
                                overview = overview.replace(" ===== 个人经历 ===== ", "\n===== 个人经历 =====\n")
                        break
                else:
                    signal.show_log_text(f"\n 🔴 Translation failed! {each.capitalize()} 不可用！")

        # 外部链接
        overview += f"\n===== 外部链接 =====\n{url_log}"
        overview = (
            overview.replace("\n", "<br>").replace("这篇报道有多个问题。请协助改善和在笔记页上的讨论。", "").strip()
        )

        # 语言替换和转换
        taglines = actor_info.taglines
        if "actor_info_zh_cn" in emby_on:
            if not taglines:
                if "AV監督" in res:
                    actor_info.taglines = ["日本成人影片导演"]
                elif "女優" in res or "女优" in res:
                    actor_info.taglines = ["日本AV女优"]
        elif "actor_info_zh_tw" in emby_on:
            if overview_req:
                overview = zhconv.convert(overview, "zh-hant")
            if tag_req:
                actor_info.taglines = [zhconv.convert(actor_info.taglines[0], "zh-hant")]
            elif "AV監督" in res:
                actor_info.taglines = ["日本成人影片導演"]
            elif "女優" in res or "女优" in res:
                actor_info.taglines = ["日本AV女優"]
        elif "actor_info_ja" in emby_on:
            overview = overview.replace("== 个人资料 ==", "== 個人情報 ==")
            overview = overview.replace("== 人物介绍 ==", "== 人物紹介 ==")
            overview = overview.replace("== 个人经历 ==", "== 個人略歴 ==")
            overview = overview.replace("== 外部链接 ==", "== 外部リンク ==")
            if not taglines:
                if "AV監督" in res:
                    actor_info.taglines = ["日本のAV監督"]
                elif "女優" in res or "女优" in res:
                    actor_info.taglines = ["日本のAV女優"]
        actor_info.overview = overview

        # 显示信息
        taglines = actor_info.taglines
        date = actor_info.birthday
        locations = actor_info.locations
        signal.show_log_text(f"👩🏻 {actor_info.name}")
        if taglines:
            signal.show_log_text(f"{taglines[0]}")
        if date and locations:
            signal.show_log_text(f"出生: {date} 在 {locations[0]}")
        if overview:
            signal.show_log_text(f"\n{overview}")
    except:
        signal.show_log_text(traceback.format_exc())
    return True


def _search_wiki(actor_info: EMbyActressInfo):
    actor_name = actor_info.name
    # 优先用日文去查找，其次繁体。wiki的搜索很烂，因为跨语言的原因，经常找不到演员
    actor_data = resources.get_actor_data(actor_name)
    actor_name_tw = ""
    if actor_data["has_name"]:
        actor_name = actor_data["jp"]
        actor_name_tw = actor_data["zh_tw"]
        if actor_name_tw == actor_name:
            actor_name_tw = ""
    else:
        actor_name = zhconv.convert(actor_name, "zh-hant")

    # 请求维基百科搜索页接口
    url = f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={actor_name}&language=zh&format=json"
    # https://www.wikidata.org/w/api.php?action=wbsearchentities&search=夢乃あいか&language=zh&format=json
    # https://www.wikidata.org/w/api.php?action=wbsearchentities&search=吉根柚莉愛&language=zh&format=json
    signal.show_log_text(f" 🌐 请求搜索页: {url}")
    head, res = get_html(url, json_data=True)
    if not head:
        signal.show_log_text(f" 🔴 维基百科搜索结果请求失败！\n    错误信息: {res}")
        return
    try:
        search_results = res.get("search")

        # 搜索无结果
        if not search_results:
            if not actor_name_tw:
                signal.show_log_text(" 🔴 维基百科暂未收录!")
                return
            url = f"https://www.wikidata.org/w/api.php?action=wbsearchentities&search={actor_name_tw}&language=zh&format=json"
            signal.show_log_text(f" 🌐 尝试再次搜索: {url}")
            head, res = get_html(url, json_data=True)
            if not head:
                signal.show_log_text(f" 🔴 维基百科搜索结果请求失败！\n    错误信息: {res}")
                return
            search_results = res.get("search")
            # 搜索无结果
            if not search_results:
                signal.show_log_text(" 🔴 维基百科暂未收录!")
                return

        for each_result in search_results:
            description = each_result.get("description")
            match_name = each_result.get("match")
            if match_name:
                temp_name = match_name.get("text")
                signal.show_log_text(f" 👩🏻 匹配名字: {temp_name}")

            # 根据描述信息判断是否为女优
            if description:
                description_en = description
                description_t = description.lower()
                signal.show_log_text(f" 📄 描述信息: {description}")
                for each_des in config.actress_wiki_keywords:
                    if each_des.lower() in description_t:
                        signal.show_log_text(f" 🎉 描述命中关键词: {each_des}")
                        break
                else:
                    signal.show_log_text(" 🔴 描述未命中关键词，识别为非女优，跳过！")
                    continue
                actor_info.taglines = [f"{description}"]
            else:
                signal.show_log_text(" 💡 不存在描述信息，尝试请求页面内容进行匹配！")
                description_en = ""

            # 通过id请求数据，获取 wiki url
            wiki_id = each_result.get("id")
            url = f"https://m.wikidata.org/wiki/Special:EntityData/{wiki_id}.json"
            # https://m.wikidata.org/wiki/Special:EntityData/Q24836820.json
            # https://m.wikidata.org/wiki/Special:EntityData/Q76283484.json
            signal.show_log_text(f" 🌐 请求 ID 数据: {url}")
            head, res = get_html(url, json_data=True)
            if not head:
                signal.show_log_text(f" 🔴 通过 id 获取 wiki url 失败！\n    错误信息: {res}")
                continue

            # 更新 descriptions
            description_zh = ""
            description_ja = ""
            try:
                descriptions = res["entities"][wiki_id]["descriptions"]
                if descriptions:
                    try:
                        description_zh = descriptions["zh"]["value"]
                    except:
                        signal.show_traceback_log(traceback.format_exc())
                    try:
                        description_ja = descriptions["ja"]["value"]
                    except:
                        signal.show_traceback_log(traceback.format_exc())
                    if description_en:
                        if not description_zh:
                            en_zh = {
                                "Japanese AV idol": "日本AV女优",
                                "Japanese pornographic actress": "日本AV女优",
                                "Japanese idol": "日本偶像",
                                "Japanese pornographic film director": "日本AV影片导演",
                                "Japanese film director": "日本电影导演",
                                "pornographic actress": "日本AV女优",
                                "Japanese actress": "日本AV女优",
                                "gravure idol": "日本写真偶像",
                            }
                            temp_zh = en_zh.get(description_en)
                            if temp_zh:
                                description_zh = temp_zh
                        if not description_ja:
                            en_ja = {
                                "Japanese AV idol": "日本のAVアイドル",
                                "Japanese pornographic actress": "日本のポルノ女優",
                                "Japanese idol": "日本のアイドル",
                                "Japanese pornographic film director": "日本のポルノ映画監督",
                                "Japanese film director": "日本の映画監督",
                                "pornographic actress": "日本のAVアイドル",
                                "Japanese actress": "日本のAVアイドル",
                                "gravure idol": "日本のグラビアアイドル",
                            }
                            temp_ja = en_ja.get(description_en)
                            if temp_ja:
                                description_ja = temp_ja
            except:
                signal.show_traceback_log(traceback.format_exc())

            # 获取 Tmdb，Imdb，Twitter，Instagram等id
            url_log = ""
            try:
                claims = res["entities"][wiki_id]["claims"]
            except:
                signal.show_traceback_log(traceback.format_exc())
                claims = None
            if claims:
                try:
                    tmdb_id = claims["P4985"][0]["mainsnak"]["datavalue"]["value"]
                    actor_info.provider_ids["Tmdb"] = tmdb_id
                    url_log += f"TheMovieDb: https://www.themoviedb.org/person/{tmdb_id} \n"
                except:
                    signal.show_traceback_log(traceback.format_exc())
                try:
                    imdb_id = claims["P345"][0]["mainsnak"]["datavalue"]["value"]
                    actor_info.provider_ids["Imdb"] = imdb_id
                    url_log += (f"IMDb: https://www.imdb.com/name/{imdb_id} \n",)
                except:
                    signal.show_traceback_log(traceback.format_exc())
                try:
                    twitter_id = claims["P2002"][0]["mainsnak"]["datavalue"]["value"]
                    actor_info.provider_ids["Twitter"] = twitter_id
                    url_log += f"Twitter: https://twitter.com/{twitter_id} \n"
                except:
                    signal.show_traceback_log(traceback.format_exc())
                try:
                    instagram_id = claims["P2003"][0]["mainsnak"]["datavalue"]["value"]
                    actor_info.provider_ids["Instagram"] = instagram_id
                    url_log += f"Instagram: https://www.instagram.com/{instagram_id} \n"
                except:
                    signal.show_traceback_log(traceback.format_exc())
                try:
                    fanza_id = claims["P9781"][0]["mainsnak"]["datavalue"]["value"]
                    actor_info.provider_ids["Fanza"] = fanza_id
                    url_log += f"Fanza: https://actress.dmm.co.jp/-/detail/=/actress_id={fanza_id} \n"
                except:
                    signal.show_traceback_log(traceback.format_exc())
                try:
                    xhamster_id = claims["P8720"][0]["mainsnak"]["datavalue"]["value"]
                    actor_info.provider_ids["xHamster"] = f"https://xhamster.com/pornstars/{xhamster_id}"
                    url_log += f"xHamster: https://xhamster.com/pornstars/{xhamster_id} \n"
                except:
                    signal.show_traceback_log(traceback.format_exc())

            # 获取 wiki url 和 description
            try:
                sitelinks = res["entities"][wiki_id]["sitelinks"]
                if sitelinks:
                    jawiki = sitelinks.get("jawiki")
                    zhwiki = sitelinks.get("zhwiki")
                    ja_url = jawiki.get("url") if jawiki else ""
                    zh_url = zhwiki.get("url") if zhwiki else ""
                    url_final = ""
                    emby_on = config.emby_on
                    if "actor_info_zh_cn" in emby_on:
                        if zh_url:
                            url_final = zh_url.replace("zh.wikipedia.org/wiki/", "zh.m.wikipedia.org/zh-cn/")
                        elif ja_url:
                            url_final = ja_url.replace("ja.", "ja.m.")

                        if description_zh:
                            description_zh = zhconv.convert(description_zh, "zh-cn")
                            actor_info.taglines = [f"{description_zh}"]
                        else:
                            if description_ja:
                                actor_info.taglines = [f"{description_ja}"]
                            elif description_en:
                                actor_info.taglines = [f"{description_en}"]
                            if "actor_info_translate" in emby_on and (description_ja or description_en):
                                actor_info.taglines_translate = True

                    elif "actor_info_zh_tw" in emby_on:
                        if zh_url:
                            url_final = zh_url.replace("zh.wikipedia.org/wiki/", "zh.m.wikipedia.org/zh-tw/")
                        elif ja_url:
                            url_final = ja_url.replace("ja.", "ja.m.")

                        if description_zh:
                            description_zh = zhconv.convert(description_zh, "zh-hant")
                            actor_info.taglines = [f"{description_zh}"]
                        else:
                            if description_ja:
                                actor_info.taglines = [f"{description_ja}"]
                            elif description_en:
                                actor_info.taglines = [f"{description_en}"]

                            if "actor_info_translate" in emby_on and (description_ja or description_en):
                                actor_info.taglines_translate = True

                    elif ja_url:
                        url_final = ja_url.replace("ja.", "ja.m.")
                        if description_ja:
                            actor_info.taglines = [f"{description_ja}"]
                        elif description_zh:
                            actor_info.taglines = [f"{description_zh}"]
                        elif description_en:
                            actor_info.taglines = [f"{description_en}"]

                    if url_final:
                        url_unquote = urllib.parse.unquote(url_final)
                        url_log += f"Wikipedia: {url_unquote}"
                        signal.show_log_text(f" 🌐 请求演员页: {url_final}")
                        return url_final, url_log
                    else:
                        signal.show_log_text(" 🔴 维基百科未获取到演员页 url！")
                    return
            except:
                signal.show_traceback_log(traceback.format_exc())

    except:
        signal.show_log_text(traceback.format_exc())


def _deal_kodi_actors(gfriends_actor_data, add):
    vedio_path = get_movie_path_setting()[0]
    if vedio_path == "" or not os.path.isdir(vedio_path):
        signal.show_log_text("🔴 待刮削目录不存在！任务已停止！")
        return False
    else:
        actor_folder = resources.userdata_path("actor")
        emby_on = config.emby_on
        all_files = os.walk(vedio_path)
        all_actor = set()
        success = set()
        failed = set()
        download_failed = set()
        no_pic = set()
        actor_clear = set()
        for root, dirs, files in all_files:
            if not add:
                for each_dir in dirs:
                    if each_dir == ".actors":
                        kodi_actor_folder = os.path.join(root, each_dir)
                        shutil.rmtree(kodi_actor_folder, ignore_errors=True)
                        signal.show_log_text(f"✅ 头像文件夹已清理！{kodi_actor_folder}")
                        actor_clear.add(kodi_actor_folder)
                continue
            for file in files:
                if file.lower().endswith(".nfo"):
                    nfo_path = os.path.join(root, file)
                    vedio_actor_folder = os.path.join(root, ".actors")
                    try:
                        with open(nfo_path, encoding="utf-8") as f:
                            content = f.read()
                        parser = etree.HTMLParser(encoding="utf-8")
                        xml_nfo = etree.HTML(content.encode("utf-8"), parser)
                        actor_list = xml_nfo.xpath("//actor/name/text()")
                        for each in actor_list:
                            all_actor.add(each)
                            actor_name_list = resources.get_actor_data(each)["keyword"]
                            for actor_name in actor_name_list:
                                if actor_name:
                                    net_pic_path = gfriends_actor_data.get(f"{actor_name}.jpg")
                                    if net_pic_path:
                                        vedio_actor_path = os.path.join(vedio_actor_folder, each + ".jpg")
                                        if os.path.isfile(vedio_actor_path):
                                            if "actor_replace" not in emby_on:
                                                success.add(each)
                                                continue
                                        if "https://" in net_pic_path:
                                            net_file_name = net_pic_path.split("/")[-1]
                                            net_file_name = re.findall(r"^[^?]+", net_file_name)[0]
                                            local_file_path = os.path.join(actor_folder, net_file_name)
                                            if not os.path.isfile(local_file_path):
                                                if not download_file_with_filepath(
                                                    net_pic_path, local_file_path, actor_folder
                                                ):
                                                    signal.show_log_text(
                                                        f"🔴 {actor_name} 头像下载失败！{net_pic_path}"
                                                    )
                                                    failed.add(each)
                                                    download_failed.add(each)
                                                    continue
                                        else:
                                            local_file_path = net_pic_path
                                        if not os.path.isdir(vedio_actor_folder):
                                            os.mkdir(vedio_actor_folder)
                                        copy_file(local_file_path, vedio_actor_path)
                                        signal.show_log_text(f"✅ {actor_name} 头像已创建！ {vedio_actor_path}")
                                        success.add(each)
                                        break
                            else:
                                signal.show_log_text(f"🔴 {each} 没有头像资源！")
                                failed.add(each)
                                no_pic.add(each)
                    except:
                        signal.show_traceback_log(traceback.format_exc())
        if add:
            signal.show_log_text(
                f"\n🎉 操作已完成! 共有演员: {len(all_actor)}, 已有头像: {len(success)}, 没有头像: {len(failed)}, 下载失败: {len(download_failed)}, 没有资源: {len(no_pic)}"
            )
        else:
            signal.show_log_text(f"\n🎉 操作已完成! 共清理了 {len(actor_clear)} 个 .actors 文件夹!")
        return
