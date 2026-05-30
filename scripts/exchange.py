import httpx
import asyncio
import json
from datetime import datetime, timedelta
import ntplib
from concurrent.futures import ThreadPoolExecutor
from .log import log_message
from .captcha import get_geeTestData
import global_vars


task_messages = []

CALIBRATE_WINDOW_SECONDS = 600  # 进入目标前 10 分钟(600s)才开始 NTP 校准，更远只用本地时间粗等


executor = ThreadPoolExecutor()

class ExchangeTask:
    def __init__(self, task):
        self.payload = task["payload"]
        self.headers = task["headers"]
        self.target_time = datetime.fromisoformat(task["time"])
        self.name = task["name"]
        self.count = task["count"]
        self.offset_ms = task.get("offset_ms", 0)  # 兑换时间偏移（毫秒，负=提早，正=延迟）
        self.status = "等待中"  # 任务状态文本，供 get_task_status 读取
        self.remaining_seconds = None  # 距实际触发的剩余秒数
        self.task_messages = []
        self.executor = ThreadPoolExecutor()
        self.task_running = True

    async def get_ntp_time(self):
        """
        获取NTP时间
        """
        client = ntplib.NTPClient()

        def fetch_ntp_time():
            try:
                response = client.request('ntp.aliyun.com')
                # 时区转换
                return datetime.utcfromtimestamp(response.tx_time) + timedelta(hours=8)
            except Exception as e:
                return None

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.executor, fetch_ntp_time)




    async def orderbeforeCreate(headers):
        url = "https://api.kurobbs.com/encourage/order/beforeCreate"
        async with httpx.AsyncClient() as client:
            try:
                res = await client.post(url, headers=headers)
                #print(res.text)
                return None
            except Exception as e:
                log_message(f"发起order前置步骤失败:{e}")
                return None
        
    async def exchange_goods(self):
        """
        兑换商品
        """
        url = "https://api.kurobbs.com/encourage/order/create"

        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, data=self.payload, headers=self.headers)
                self.task_messages.append(f"任务 {self.name}返回：{response.text}")
                log_message(f"任务 {self.name}返回：{response.text}")
            except httpx.HTTPStatusError as e:
                self.task_messages.append(f"任务 {self.name}HTTP error occurred: {e}")
                log_message(f"任务 {self.name}HTTP error occurred: {e}")
            except Exception as e:
                self.task_messages.append(f"任务 {self.name}An error occurred: {e}")
                log_message(f"任务 {self.name}An error occurred: {e}")

    async def schedule_task(self):
        """
        调度任务
        """
        # 应用毫秒级偏移：负值提早、正值延迟，统一并入目标时间
        effective_target = self.target_time + timedelta(milliseconds=self.offset_ms)
        log_message(f"任务 {self.name} 已启动, 目标时间 {self.target_time}, 偏移 {self.offset_ms}ms, 实际触发 {effective_target}")
        while self.task_running:
            # 距目标 >10 分钟时用本地时间粗等，不请求 NTP；临近(<10分钟)才开始校准
            local_remaining = (effective_target - datetime.now()).total_seconds()
            if local_remaining > CALIBRATE_WINDOW_SECONDS:
                self.status = "等待中"
                self.remaining_seconds = local_remaining
                # 分段睡眠(每轮≤60s)逼近校准窗口，同时保证 1 秒内能响应停止
                sleep_secs = max(1, int(min(local_remaining - CALIBRATE_WINDOW_SECONDS, 60)))
                for _ in range(sleep_secs):
                    if not self.task_running:
                        break
                    await asyncio.sleep(1)
                continue
            ntp_time = await self.get_ntp_time()
            if ntp_time:
                self.task_messages.append(f"现在是北京时间： {ntp_time}")
                delay = (effective_target - ntp_time).total_seconds()
                self.remaining_seconds = delay
                # 注意 由于日志输出使用的是电脑时间 而兑换时间使用的是ntp时间 所以日志上的时间会有所偏差
                if delay <= 60:
                    self.status = "即将兑换"
                    # 提早量受限：delay 为负时立即发送（sleep 0）
                    await asyncio.sleep(max(0, delay))
                    try:
                        self.payload["geeTestData"] = get_geeTestData()
                        # 在 gather 前才创建协程，避免长期持有未 await 协程
                        tasks = [self.exchange_goods() for _ in range(self.count)]
                        self.status = "兑换中"
                        await asyncio.gather(*tasks)
                        log_message(f"{await self.get_ntp_time()} 任务 {self.name} 已执行完成")
                        # 若期间未被停止，则标记已完成
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
                    # 分段 sleep，使停止能在 1 秒内响应
                    for _ in range(30):
                        if not self.task_running:
                            break
                        await asyncio.sleep(1)
            else:
                self.status = "NTP失败"
                self.task_messages.append("获取NTP时间失败. 1秒后重试")
                await asyncio.sleep(1)