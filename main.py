import aiohttp
import asyncio
import datetime
from typing import Dict
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger


@register("astrbot_plugin_api_caller", "YourName", "API调用 + 定时发送插件", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

        # 从 WebUI 插件配置中读取
        self.api_base_url = self.config.get("api_base_url", "https://api.nycnm.cn/API/weather.php")
        self.api_key = self.config.get("api_key", "")

        # 存储所有定时任务
        self.scheduled_tasks: Dict[str, dict] = {}
        self.task_counter = 0

    async def initialize(self):
        logger.info("天气API插件已加载")

    # ══════════════════════════════════════════
    # 工具方法：调用 API
    # ══════════════════════════════════════════

    async def fetch_text(self, keyword: str) -> str:
        params = {"q": keyword}
        if self.api_key:
            params["token"] = self.api_key
        async with aiohttp.ClientSession() as session:
            async with session.get(
                self.api_base_url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    return f"❌ 请求失败，状态码：{resp.status}"
                data = await resp.json()
                return data.get("result", "API 没有返回内容")

    async def fetch_image_url(self, keyword: str) -> str:
        params = {"q": keyword}
        if self.api_key:
            params["token"] = self.api_key
        async with aiohttp.ClientSession() as session:
            async with session.get(
                self.api_base_url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=20)
            ) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json()
                return data.get("image_url", "")

    # ══════════════════════════════════════════
    # 手动调用指令（所有人可用）
    # ══════════════════════════════════════════

    @filter.command("天气text")
    async def api_text(self, event: AstrMessageEvent):
        """调用 API 返回文本，用法：/天气text <城市>"""
        keyword = event.message_str.strip()
        if not keyword:
            yield event.plain_result("❌ 用法：/天气text <城市>，例如：/天气text 北京")
            return
        yield event.plain_result("⏳ 请求中...")
        try:
            result = await self.fetch_text(keyword)
            yield event.plain_result(result)
        except Exception as e:
            logger.error(f"[天气text] {e}")
            yield event.plain_result(f"❌ 错误：{e}")

    @filter.command("天气img")
    async def api_image(self, event: AstrMessageEvent):
        """调用 API 返回图片，用法：/天气img <城市>"""
        keyword = event.message_str.strip()
        if not keyword:
            yield event.plain_result("❌ 用法：/天气img <城市>，例如：/天气img 北京")
            return
        yield event.plain_result("⏳ 图片获取中...")
        try:
            url = await self.fetch_image_url(keyword)
            if url:
                yield event.image_result(url)
            else:
                yield event.plain_result("❌ 未获取到图片")
        except Exception as e:
            logger.error(f"[天气img] {e}")
            yield event.plain_result(f"❌ 错误：{e}")

    # ══════════════════════════════════════════
    # 帮助指令（所有人可用）
    # ══════════════════════════════════════════

    @filter.command("天气帮助")
    async def help_cmd(self, event: AstrMessageEvent):
        """发送使用帮助并引导操作"""
        yield event.plain_result(
            "📖 天气API插件使用说明\n"
            "\n"
            "━━━━━━ 所有人可用 ━━━━━━\n"
            "🔹 /天气text <城市>  获取文字天气\n"
            "🔹 /天气img <城市>   获取天气图片\n"
            "\n"
            "━━━━━━ 管理员专用 ━━━━━━\n"
            "🔸 /定时 add time <HH:MM> <text|image> <城市>\n"
            "🔸 /定时 add interval <分钟> <text|image> <城市>\n"
            "🔸 /定时 del <ID>  删除定时任务\n"
            "🔸 /定时 list      查看所有定时任务\n"
            "\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "请选择你想做的操作：\n"
            "  1️⃣  查看使用示例\n"
            "  2️⃣  查看任务列表\n"
            "  3️⃣  删除任务\n"
            "  0️⃣  退出"
        )

        try:
            resp = await event.wait_for_reply(timeout=30)
            choice = resp.message_str.strip()
        except TimeoutError:
            yield event.plain_result("⏰ 等待超时，已退出帮助菜单")
            return

        if choice == "1":
            yield event.plain_result(
                "📌 使用示例\n"
                "\n"
                "查询文字天气：\n"
                "  /天气text 北京\n"
                "\n"
                "查询天气图片：\n"
                "  /天气img 上海\n"
                "\n"
                "每天 08:00 推送北京天气：\n"
                "  /定时 add time 08:00 text 北京\n"
                "\n"
                "每 60 分钟推送一次天气图：\n"
                "  /定时 add interval 60 image 广州"
            )

        elif choice == "2":
            if not self.scheduled_tasks:
                yield event.plain_result("📋 当前没有定时任务")
            else:
                lines = ["📋 当前定时任务列表："]
                for tid, item in self.scheduled_tasks.items():
                    info = item["info"]
                    lines.append(
                        f"\n  ID：{tid}\n"
                        f"  模式：{info['value']}\n"
                        f"  类型：{info['type']}\n"
                        f"  关键词：{info['keyword']}"
                    )
                yield event.plain_result("\n".join(lines))

        elif choice == "3":
            if not self._check_admin(event):
                yield event.plain_result("❌ 删除任务需要管理员权限")
                return

            if not self.scheduled_tasks:
                yield event.plain_result("📋 当前没有定时任务可删除")
                return

            lines = ["请输入要删除的任务 ID："]
            for tid, item in self.scheduled_tasks.items():
                info = item["info"]
                lines.append(
                    f"  ID：{tid}  {info['value']}  "
                    f"{info['type']}  {info['keyword']}"
                )
            yield event.plain_result("\n".join(lines))

            try:
                resp2 = await event.wait_for_reply(timeout=30)
                task_id = resp2.message_str.strip()
            except TimeoutError:
                yield event.plain_result("⏰ 等待超时，已退出")
                return

            if task_id not in self.scheduled_tasks:
                yield event.plain_result(f"❌ 未找到任务 ID：{task_id}")
                return

            self.scheduled_tasks[task_id]["task"].cancel()
            del self.scheduled_tasks[task_id]
            yield event.plain_result(f"✅ 任务 {task_id} 已删除")

        elif choice == "0":
            yield event.plain_result("👋 已退出帮助菜单")

        else:
            yield event.plain_result("❌ 无效选项，请输入 0～3 的数字")

    # ══════════════════════════════════════════
    # 定时任务统一入口（管理员专用）
    # ══════════════════════════════════════════

    def _check_admin(self, event: AstrMessageEvent) -> bool:
        return event.is_admin()

    @filter.command("定时")
    async def schedule(self, event: AstrMessageEvent):
        """
        定时任务管理（管理员专用）
        /定时 add time 08:00 text 北京
        /定时 add interval 30 image 上海
        /定时 del <ID>
        /定时 list
        """
        if not self._check_admin(event):
            yield event.plain_result("❌ 该指令仅限管理员使用")
            return

        args = event.message_str.strip().split()
        sub = args[0].lower() if args else ""

        if sub == "add":
            async for result in self._schedule_add(event, args[1:]):
                yield result
        elif sub == "del":
            async for result in self._schedule_del(event, args[1:]):
                yield result
        elif sub == "list":
            async for result in self._schedule_list(event):
                yield result
        else:
            yield event.plain_result(
                "📖 定时指令用法：\n"
                "  /定时 add time 08:00 text 北京\n"
                "  /定时 add interval 30 image 上海\n"
                "  /定时 del <ID>\n"
                "  /定时 list"
            )

    # ══════════════════════════════════════════
    # 定时任务子命令实现
    # ══════════════════════════════════════════

    async def _schedule_add(self, event: AstrMessageEvent, args: list):
        if len(args) < 4:
            yield event.plain_result(
                "❌ 参数不足\n"
                "用法：\n"
                "  /定时 add time <HH:MM> <text|image> <城市>\n"
                "  /定时 add interval <分钟> <text|image> <城市>"
            )
            return

        mode = args[0].lower()
        value = args[1]
        api_type = args[2].lower()
        keyword = " ".join(args[3:])
        umo = event.unified_msg_origin

        if api_type not in ("text", "image"):
            yield event.plain_result("❌ 类型必须是 text 或 image")
            return

        self.task_counter += 1
        task_id = str(self.task_counter)

        if mode == "interval":
            try:
                interval_min = float(value)
                if interval_min <= 0:
                    raise ValueError
            except ValueError:
                yield event.plain_result("❌ 间隔必须是正数（分钟）")
                return

            task = asyncio.create_task(
                self._run_interval_task(task_id, umo, interval_min, api_type, keyword)
            )
            self.scheduled_tasks[task_id] = {
                "task": task,
                "info": {
                    "mode": "interval",
                    "value": f"每 {interval_min} 分钟",
                    "type": api_type,
                    "keyword": keyword,
                }
            }
            yield event.plain_result(
                f"✅ 定时任务已添加\n"
                f"  ID：{task_id}\n"
                f"  模式：每 {interval_min} 分钟\n"
                f"  类型：{api_type}\n"
                f"  关键词：{keyword}"
            )

        elif mode == "time":
            try:
                hour, minute = map(int, value.split(":"))
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError
            except (ValueError, AttributeError):
                yield event.plain_result("❌ 时间格式错误，请用 HH:MM，例如 08:00")
                return

            task = asyncio.create_task(
                self._run_time_task(task_id, umo, hour, minute, api_type, keyword)
            )
            self.scheduled_tasks[task_id] = {
                "task": task,
                "info": {
                    "mode": "time",
                    "value": f"每天 {hour:02d}:{minute:02d}",
                    "type": api_type,
                    "keyword": keyword,
                }
            }
            yield event.plain_result(
                f"✅ 定时任务已添加\n"
                f"  ID：{task_id}\n"
                f"  模式：每天 {hour:02d}:{minute:02d}\n"
                f"  类型：{api_type}\n"
                f"  关键词：{keyword}"
            )

        else:
            yield event.plain_result("❌ 模式必须是 time 或 interval")

    async def _schedule_del(self, event: AstrMessageEvent, args: list):
        if not args:
            yield event.plain_result("❌ 请提供任务 ID，用法：/定时 del <ID>")
            return

        task_id = args[0]
        if task_id not in self.scheduled_tasks:
            yield event.plain_result(f"❌ 未找到任务 ID：{task_id}")
            return

        self.scheduled_tasks[task_id]["task"].cancel()
        del self.scheduled_tasks[task_id]
        yield event.plain_result(f"✅ 任务 {task_id} 已删除")

    async def _schedule_list(self, event: AstrMessageEvent):
        if not self.scheduled_tasks:
            yield event.plain_result("📋 当前没有定时任务")
            return

        lines = ["📋 当前定时任务列表："]
        for tid, item in self.scheduled_tasks.items():
            info = item["info"]
            lines.append(
                f"\n  ID：{tid}\n"
                f"  模式：{info['value']}\n"
                f"  类型：{info['type']}\n"
                f"  关键词：{info['keyword']}"
            )
        yield event.plain_result("\n".join(lines))

    # ══════════════════════════════════════════
    # 后台定时任务执行逻辑
    # ══════════════════════════════════════════

    async def _send_api_result(self, umo: str, api_type: str, keyword: str):
        from astrbot.api.event import MessageChain
        try:
            if api_type == "text":
                text = await self.fetch_text(keyword)
                chain = MessageChain().message(text)
            else:
                url = await self.fetch_image_url(keyword)
                chain = MessageChain().file_image(url) if url else MessageChain().message("❌ 未获取到图片")
            await self.context.send_message(umo, chain)
        except Exception as e:
            logger.error(f"[定时发送] 出错: {e}")

    async def _run_interval_task(self, task_id: str, umo: str, interval_min: float, api_type: str, keyword: str):
        logger.info(f"[定时任务 {task_id}] 启动，间隔 {interval_min} 分钟")
        try:
            while True:
                await asyncio.sleep(interval_min * 60)
                logger.info(f"[定时任务 {task_id}] 执行中...")
                await self._send_api_result(umo, api_type, keyword)
        except asyncio.CancelledError:
            logger.info(f"[定时任务 {task_id}] 已取消")

    async def _run_time_task(self, task_id: str, umo: str, hour: int, minute: int, api_type: str, keyword: str):
        logger.info(f"[定时任务 {task_id}] 启动，每天 {hour:02d}:{minute:02d} 执行")
        try:
            while True:
                now = datetime.datetime.now()
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if target <= now:
                    target += datetime.timedelta(days=1)
                wait_sec = (target - now).total_seconds()
                logger.info(f"[定时任务 {task_id}] 距下次执行 {wait_sec:.0f} 秒")
                await asyncio.sleep(wait_sec)
                logger.info(f"[定时任务 {task_id}] 执行中...")
                await self._send_api_result(umo, api_type, keyword)
        except asyncio.CancelledError:
            logger.info(f"[定时任务 {task_id}] 已取消")

    # ══════════════════════════════════════════
    # 插件卸载
    # ══════════════════════════════════════════

    async def terminate(self):
        for task_id, item in self.scheduled_tasks.items():
            item["task"].cancel()
            logger.info(f"[定时任务 {task_id}] 已随插件卸载取消")
        self.scheduled_tasks.clear()
        logger.info("天气API插件已卸载")