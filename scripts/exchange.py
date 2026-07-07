import httpx
import asyncio
import json
import time
from datetime import datetime, timedelta
import ntplib
from concurrent.futures import ThreadPoolExecutor
from .log import log_message
from .captcha import get_geeTestData_async
import global_vars


task_messages = []

CALIBRATE_WINDOW_SECONDS = 600  # 距目标 <= 10min 才启用 NTP 校准，更远只用本地时间粗等
WARMUP_WINDOW_SECONDS = 60      # 距目标 <= 60s 时预热 TCP/TLS 与依赖库
SPIN_WAIT_SECONDS = 0.03        # 最后 30ms 用 perf_counter 忙等，规避 asyncio.sleep 精度
EXCHANGE_URL = "https://api.kurobbs.com/encourage/order/create"

executor = ThreadPoolExecutor()


class ExchangeTask:
    def __init__(self, task):
        self.payload = task["payload"]
        self.headers = task["headers"]
        self.target_time = datetime.fromisoformat(task["time"])
        self.name = task["name"]
        self.count = task["count"]
        self.offset_ms = task.get("offset_ms", 0)  # 兑换时间偏移（毫秒，负=提早，正=延迟）
        self.status = "等待中"
        self.remaining_seconds = None
        self.task_messages = []
        self.executor = ThreadPoolExecutor()
        self.task_running = True
        # 复用同一个 httpx 连接。极验(gcaptcha4)与兑换(api.kurobbs)不同域，
        # 但同一个 client 内可分别维护连接池，都能省掉第一次触发的握手时间。
        self.client = httpx.AsyncClient(http2=False, timeout=8.0)
        self._warmed_up = False

    async def get_ntp_time(self):
        client = ntplib.NTPClient()

        def fetch_ntp_time():
            try:
                response = client.request('ntp.aliyun.com')
                return datetime.utcfromtimestamp(response.tx_time) + timedelta(hours=8)
            except Exception:
                return None

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, fetch_ntp_time)

    async def _warmup(self):
        """
        触发前预热：建立到 api.kurobbs.com / gcaptcha4.geetest.com / static.geetest.com
        的 TCP+TLS 连接，加载 ddddocr 与 execjs，避免真正抢购时才现场付这些成本。
        """
        if self._warmed_up:
            return
        self._warmed_up = True
        try:
            from .utils import geeUtils
            # ddddocr / execjs 预热（同步耗时，扔线程池）
            await asyncio.get_event_loop().run_in_executor(self.executor, geeUtils.warmup)
        except Exception as e:
            log_message(f"任务 {self.name} 依赖库预热失败(忽略): {e}")

        async def _head(url):
            try:
                await self.client.head(url, timeout=3.0)
            except Exception:
                pass

        await asyncio.gather(
            _head("https://api.kurobbs.com/"),
            _head("https://gcaptcha4.geetest.com/"),
            _head("https://static.geetest.com/"),
        )
        self.task_messages.append("已预热连接与依赖库")
        log_message(f"任务 {self.name} 已预热连接与依赖库")

    async def exchange_goods(self):
        try:
            response = await self.client.post(EXCHANGE_URL, data=self.payload, headers=self.headers, timeout=8.0)
            self.task_messages.append(f"任务 {self.name}返回：{response.text}")
            log_message(f"任务 {self.name}返回：{response.text}")
            return response.text
        except httpx.HTTPStatusError as e:
            self.task_messages.append(f"任务 {self.name}HTTP error occurred: {e}")
            log_message(f"任务 {self.name}HTTP error occurred: {e}")
        except Exception as e:
            self.task_messages.append(f"任务 {self.name}An error occurred: {e}")
            log_message(f"任务 {self.name}An error occurred: {e}")
        return None

    async def _precise_sleep(self, seconds: float):
        """大段用 asyncio.sleep，最后 SPIN_WAIT_SECONDS 用 perf_counter 忙等。"""
        if seconds <= 0:
            return
        deadline = time.perf_counter() + seconds
        coarse = seconds - SPIN_WAIT_SECONDS
        if coarse > 0:
            await asyncio.sleep(coarse)
        while time.perf_counter() < deadline:
            if not self.task_running:
                return

    async def _run_exchange_burst(self):
        """
        触发时刻到来后的核心流程：
        1) 立刻并发跑极验（load→OCR→verify），拿到 seccode
        2) seccode 就绪立即连发 count 次兑换
        整个过程共用同一个 httpx.AsyncClient，握手成本已在预热阶段付掉。
        """
        t0 = time.perf_counter()
        seccode = await get_geeTestData_async(client=self.client)
        t1 = time.perf_counter()
        log_message(f"任务 {self.name} 极验耗时 {(t1 - t0) * 1000:.0f}ms, seccode={'ok' if seccode else 'FAIL'}")
        self.task_messages.append(f"极验耗时 {(t1 - t0) * 1000:.0f}ms")

        if not seccode:
            # 极验失败仍尝试发一发（有些情况下服务端可能不严格校验），
            # 但主要用来暴露风控错误码而非真的期望命中。
            log_message(f"任务 {self.name} 极验失败，仍尝试发起 {self.count} 次请求")
        self.payload["geeTestData"] = seccode or {}

        self.status = "兑换中"
        tasks = [self.exchange_goods() for _ in range(self.count)]
        await asyncio.gather(*tasks)
        t2 = time.perf_counter()
        log_message(f"任务 {self.name} 全部兑换请求发出耗时 {(t2 - t1) * 1000:.0f}ms")

    async def schedule_task(self):
        effective_target = self.target_time + timedelta(milliseconds=self.offset_ms)
        log_message(
            f"任务 {self.name} 已启动, 目标时间 {self.target_time}, 偏移 {self.offset_ms}ms, "
            f"实际触发 {effective_target}"
        )
        try:
            while self.task_running:
                local_remaining = (effective_target - datetime.now()).total_seconds()

                # 距目标 >10 分钟：粗等
                if local_remaining > CALIBRATE_WINDOW_SECONDS:
                    self.status = "等待中"
                    self.remaining_seconds = local_remaining
                    sleep_secs = max(1, int(min(local_remaining - CALIBRATE_WINDOW_SECONDS, 60)))
                    for _ in range(sleep_secs):
                        if not self.task_running:
                            break
                        await asyncio.sleep(1)
                    continue

                ntp_time = await self.get_ntp_time()
                if not ntp_time:
                    self.status = "NTP失败"
                    self.task_messages.append("获取NTP时间失败. 1秒后重试")
                    await asyncio.sleep(1)
                    continue

                self.task_messages.append(f"现在是北京时间： {ntp_time}")
                delay = (effective_target - ntp_time).total_seconds()
                self.remaining_seconds = delay

                # 进入 60s 窗口就预热一次（幂等）
                if delay <= WARMUP_WINDOW_SECONDS:
                    self.status = "预热中"
                    await self._warmup()

                if delay <= 60:
                    self.status = "即将兑换"
                    # 预热花时间，重新做一次 NTP 校准
                    ntp_time2 = await self.get_ntp_time()
                    if ntp_time2:
                        delay = (effective_target - ntp_time2).total_seconds()
                        self.remaining_seconds = delay

                    await self._precise_sleep(delay)
                    try:
                        await self._run_exchange_burst()
                        log_message(f"{await self.get_ntp_time()} 任务 {self.name} 已执行完成")
                        if self.status != "已停止":
                            self.status = "已完成"
                    except Exception as e:
                        self.status = "出错"
                        self.task_messages.append(f"任务执行出错: {e}")
                        log_message(f"任务 {self.name} 执行出错: {e}")
                    finally:
                        self.remaining_seconds = 0
                        self.task_running = False
                    break
                else:
                    self.status = "倒计时"
                    self.task_messages.append(f"目前还剩余 {delay} 秒. 30秒后重新校准时间")
                    for _ in range(30):
                        if not self.task_running:
                            break
                        await asyncio.sleep(1)
        finally:
            try:
                await self.client.aclose()
            except Exception:
                pass
