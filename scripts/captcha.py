from .utils import geeUtils
import time

CAPTCHA_ID = "3f7e2d848ce0cb7e7d019d621e556ce2"


def getCurrentStampMs():
    return round(time.time() * 1000)


def get_geeTestData():
    """同步版：兜底/旧路径使用。抢购路径请用 get_geeTestData_async。"""
    callBackSign = f"geetest_{getCurrentStampMs()}"
    seccode = geeUtils.geeSecCode(callBackSign=callBackSign, captcha_id=CAPTCHA_ID)
    return seccode


async def get_geeTestData_async(client=None):
    """
    异步版：抢购路径调用，复用传入的 httpx.AsyncClient 共享连接池。
    """
    callBackSign = f"geetest_{getCurrentStampMs()}"
    seccode = await geeUtils.geeSecCodeAsync(callBackSign=callBackSign, captcha_id=CAPTCHA_ID, client=client)
    return seccode
