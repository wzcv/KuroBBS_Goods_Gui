import json
import os
import global_vars
from datetime import datetime
import socket


# 获取当前文件的绝对路径
base_dir = os.path.abspath(os.path.dirname(__file__))
parent_dir = os.path.dirname(base_dir)
goodslist_path = os.path.join(parent_dir, 'goodslist.json')
config_path = os.path.join(parent_dir, 'config.json')
tasklistpath = os.path.join(parent_dir, 'tasklist.json')




def convert_timestamp_to_string(timestamp_ms:int):
    """
    转化时间戳为字符串
    """
    dt_object = datetime.fromtimestamp(timestamp_ms / 1000)
    formatted_date = dt_object.strftime('%Y-%m-%dT%H:%M:%S')
    return formatted_date


def get_ip_address():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 连接到一个公共的服务器，这里使用 Google 的 DNS 服务器
        s.connect(('8.8.8.8', 80))
        ip_address = s.getsockname()[0]
    except socket.error:
        ip_address = '127.0.0.1'
    finally:
        s.close()
    return ip_address


def add_to_wishlist(commodityCode, saleTime, commodityName, gameId):
    """
    将商品添加到备选清单中
    """
    try:
        with open(goodslist_path, 'r', encoding='utf-8') as f:
            goods_list = json.load(f)
            print(goods_list)
    except (FileNotFoundError, json.JSONDecodeError):
        print("Failed to read goodslist.json")
        goods_list = []

    # 添加新商品到备选清单
    new_item = {
        "commodityName": commodityName,
        "commodityCode": commodityCode,
        "saleTime": saleTime,
        "gameId":gameId
    }
    goods_list.append(new_item)

    # 写入goodslist.json文件
    with open(goodslist_path, 'w', encoding='utf-8') as f:
        json.dump(goods_list, f, ensure_ascii=False, indent=4)


def clear_goodslist():
    with open(goodslist_path, 'w') as file:
        file.write('')

def clear_tasklist():
    with open(tasklistpath, 'w') as file:
        file.write('')


def delete_task(name):
    """
    从任务清单中删除指定名称的任务，返回删除后的列表
    """
    try:
        with open(tasklistpath, 'r', encoding='utf-8') as file:
            tasklist = json.load(file)
    except Exception:
        tasklist = []
    tasklist = [t for t in tasklist if t.get('name') != name]
    with open(tasklistpath, 'w', encoding='utf-8') as file:
        json.dump(tasklist, file, ensure_ascii=False, indent=4)
    return tasklist


def format_cookie_string(cookie):
    return '; '.join([f"{key}={value}" for key, value in cookie.items()])



def _seq_of(task):
    """解析任务的整数序号；非数字(历史遗留名称)返回 None。"""
    try:
        return int(task.get('name'))
    except (TypeError, ValueError):
        return None


def next_seq(tasklist):
    """返回最小可用正整数序号：删除任务后其序号自动释放，可被后续新建复用。"""
    used = {s for s in (_seq_of(t) for t in tasklist) if s is not None}
    n = 1
    while n in used:
        n += 1
    return n


def sort_key(task):
    """任务排序键：数字序号升序在前，非数字名称(历史遗留)排其后。"""
    s = _seq_of(task)
    return (0, s) if s is not None else (1, str(task.get('name', '')))


def add_to_tasklist(commodityCode:str,address:dict,gameId:str,time:str,count:int,offset_ms:int=0):
    """
    将任务添加到任务清单中，自动分配序号(name)并返回。
    offset_ms: 兑换时间偏移（毫秒，负值=提早，正值=延迟）
    """

    payload = {
        "commodityCode": commodityCode,
        "commodityNum": "1",
        "geeTestData": {}, # 该字段先空着 到兑换前再填
        "province": address["province"],
        "city": address["city"],
        "area": address["district"],
        "detail": address["fullAddress"],
        "mobile": int(address["tel"]),
        "receiver": address["name"],
        "gameId": gameId
    }
    headers = {
        "devcode":  global_vars.devcode,
        "ip": "219.142.99.8",
        "source": "android",
        "version": "2.2.1",
        "versioncode": "2210",
        "token": global_vars.token,
        "osversion": "Android",
        "distinct_id": global_vars.distinct_id,
        "countrycode": "CN",
        "model": "2203121C",
        "lang": "zh-Hans",
        "channelid": "2",
        "content-type": "application/x-www-form-urlencoded",
        "accept-encoding": "gzip",
        "cookie": "user_token="+global_vars.token,
        "user-agent": "okhttp/3.11.0"
    }
    try:
        with open(tasklistpath, 'r', encoding='utf-8') as file:
            tasklist = json.load(file)
    except Exception as e:
        tasklist = []
        print(f"Failed to read tasklist.json: {str(e)}")

    # 取消用户自定义任务名：自动分配最小可用序号作为唯一标识
    name = str(next_seq(tasklist))
    task = {
        "name": name,
        "payload": payload,
        "headers": headers,
        "time": time,
        "count": count,
        "offset_ms": offset_ms
    }
    tasklist.append(task)

    # Save the updated tasklist back to tasklist.json
    with open(tasklistpath, 'w', encoding='utf-8') as file:
        json.dump(tasklist, file, ensure_ascii=False, indent=4)

    return name

