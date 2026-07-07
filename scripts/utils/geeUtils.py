import asyncio
import json
import urllib.parse
import uuid

import execjs
import httpx
import requests
import ddddocr
from loguru import logger
import os
from . import trackUtils

current_dir = os.path.dirname(os.path.abspath(__file__))
js_path = os.path.join(current_dir, '..', 'geeTest.js')

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
}

with open(js_path, 'r') as f:
    geeTestText = f.read()

# ---------------------------------------------------------------------------
# 全局单例：ddddocr 模型加载 + execjs 上下文编译各要花几百毫秒，
# 抢购时不能在触发路径上再付这笔成本，因此提前初始化并复用同一个实例。
# ---------------------------------------------------------------------------
_OCR = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
_JS_CTX = execjs.compile(geeTestText)


def warmup():
    """
    进程启动阶段调用一次，触发 ddddocr / execjs 内部的懒加载，
    这样第一次真正抢购时才不会额外等 300~800ms。
    """
    try:
        # 用一个极小的空白图片走一遍 slide_match，让模型 warmup
        blank = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
            b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx"
            b"\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfeA\x89\x89S\x00\x00\x00"
            b"\x00IEND\xaeB`\x82"
        )
        try:
            _OCR.slide_match(target_bytes=blank, background_bytes=blank, simple_target=True)
        except Exception:
            pass
        _JS_CTX.call("geeTestW", 0, 0, "warmup", "20240101000000", "warmup")
    except Exception:
        # 预热失败不影响主流程
        pass


def _convertCallBack(callBackSign: str, context: str):
    return json.loads(context[len(callBackSign) + 1: len(context) - 1])


# =========================
# 同步版本（保留旧接口向后兼容）
# =========================
def geeLoad(callBackSign: str, captcha_id: str, challenge: str):
    url = "https://gcaptcha4.geetest.com/load"
    params = {
        "callback": callBackSign,
        "captcha_id": captcha_id,
        "challenge": challenge,
        "client_type": "android",
        "lang": "zh"
    }
    response = requests.get(url, headers=headers, params=params)
    return _convertCallBack(callBackSign=callBackSign, context=response.text)


def geeSlideAnalyse(bgPath: str, slicePath: str):
    geeHost = 'https://static.geetest.com/'
    targetUrl = urllib.parse.urljoin(geeHost, slicePath)
    bgUrl = urllib.parse.urljoin(geeHost, bgPath)
    targetBytes = requests.get(url=targetUrl, headers=headers).content
    bgBytes = requests.get(url=bgUrl, headers=headers).content
    target = _OCR.slide_match(target_bytes=targetBytes, background_bytes=bgBytes, simple_target=True)['target']
    distance = target[0]
    sliceTime = trackUtils.GetSlideTrackTime(distance=distance)
    return {"distance": distance, "time": sliceTime}


def geeSecCode(callBackSign: str, captcha_id: str, max_retry: int = 1):
    """
    同步版兜底接口。抢购路径改用 geeSecCodeAsync，这里默认 max_retry=1 避免拖时间。
    """
    for attempt in range(max_retry):
        challenge = str(uuid.uuid4())
        try:
            geeLoadData = geeLoad(callBackSign=callBackSign, captcha_id=captcha_id, challenge=challenge)['data']
            geeDetectInfo = geeSlideAnalyse(bgPath=geeLoadData['bg'], slicePath=geeLoadData['slice'])
            lotNumber = geeLoadData['lot_number']
            w = _JS_CTX.call(
                "geeTestW",
                geeDetectInfo['distance'], geeDetectInfo['time'], lotNumber,
                geeLoadData['pow_detail']['datetime'], captcha_id,
            )
            params = {
                "callback": callBackSign,
                "captcha_id": captcha_id,
                "challenge": challenge,
                "client_type": "android",
                "lot_number": lotNumber,
                "payload": geeLoadData['payload'],
                "process_token": geeLoadData['process_token'],
                "payload_protocol": "1",
                "pt": "1",
                "w": w,
            }
            response = requests.get(url="https://gcaptcha4.geetest.com/verify", params=params, headers=headers)
            responseJson = _convertCallBack(callBackSign=callBackSign, context=response.text)
            if responseJson['data']['result'] == "success":
                return json.dumps(responseJson['data']['seccode'])
            logger.warning(f"第 {attempt+1}/{max_retry} 次滑块验证失败(distance={geeDetectInfo['distance']})")
        except Exception as e:
            logger.warning(f"第 {attempt+1}/{max_retry} 次极验流程异常: {e}")
    logger.error("滑块验证失败，请重试")
    return None


# =========================
# 异步版本（抢购路径专用）
# =========================
async def _geeLoadAsync(client: httpx.AsyncClient, callBackSign: str, captcha_id: str, challenge: str):
    params = {
        "callback": callBackSign,
        "captcha_id": captcha_id,
        "challenge": challenge,
        "client_type": "android",
        "lang": "zh",
    }
    resp = await client.get("https://gcaptcha4.geetest.com/load", headers=headers, params=params, timeout=5.0)
    return _convertCallBack(callBackSign=callBackSign, context=resp.text)


async def _geeSlideAnalyseAsync(client: httpx.AsyncClient, bgPath: str, slicePath: str, loop):
    """
    bg / slice 并发下载 + OCR 识别放线程池，避免阻塞事件循环。
    """
    geeHost = 'https://static.geetest.com/'
    targetUrl = urllib.parse.urljoin(geeHost, slicePath)
    bgUrl = urllib.parse.urljoin(geeHost, bgPath)

    async def _get(u):
        r = await client.get(u, headers=headers, timeout=5.0)
        return r.content

    targetBytes, bgBytes = await asyncio.gather(_get(targetUrl), _get(bgUrl))

    def _match():
        return _OCR.slide_match(target_bytes=targetBytes, background_bytes=bgBytes, simple_target=True)['target']

    target = await loop.run_in_executor(None, _match)
    distance = target[0]
    sliceTime = trackUtils.GetSlideTrackTime(distance=distance)
    return {"distance": distance, "time": sliceTime}


async def geeSecCodeAsync(callBackSign: str, captcha_id: str, client: httpx.AsyncClient = None):
    """
    抢购路径专用：一次成即返回，不重试不重滑块（重试会拖 2~5 秒，抢购里承担不起）。
    :param client: 复用外部 httpx.AsyncClient，避免建连接开销。
    """
    own_client = False
    if client is None:
        client = httpx.AsyncClient(http2=False, timeout=5.0)
        own_client = True
    loop = asyncio.get_event_loop()
    try:
        challenge = str(uuid.uuid4())
        geeLoadData = (await _geeLoadAsync(client, callBackSign, captcha_id, challenge))['data']
        geeDetectInfo = await _geeSlideAnalyseAsync(client, geeLoadData['bg'], geeLoadData['slice'], loop)
        lotNumber = geeLoadData['lot_number']

        def _calc_w():
            return _JS_CTX.call(
                "geeTestW",
                geeDetectInfo['distance'], geeDetectInfo['time'], lotNumber,
                geeLoadData['pow_detail']['datetime'], captcha_id,
            )

        w = await loop.run_in_executor(None, _calc_w)

        params = {
            "callback": callBackSign,
            "captcha_id": captcha_id,
            "challenge": challenge,
            "client_type": "android",
            "lot_number": lotNumber,
            "payload": geeLoadData['payload'],
            "process_token": geeLoadData['process_token'],
            "payload_protocol": "1",
            "pt": "1",
            "w": w,
        }
        resp = await client.get("https://gcaptcha4.geetest.com/verify", params=params, headers=headers, timeout=5.0)
        responseJson = _convertCallBack(callBackSign=callBackSign, context=resp.text)
        if responseJson['data']['result'] == "success":
            return json.dumps(responseJson['data']['seccode'])
        logger.warning(f"滑块验证失败(distance={geeDetectInfo['distance']})")
        return None
    except Exception as e:
        logger.warning(f"极验异步流程异常: {e}")
        return None
    finally:
        if own_client:
            await client.aclose()
