import asyncio
import aiohttp
import tempfile
import os
import json
import datetime
import zoneinfo
from urllib.parse import quote
from pathlib import Path
from typing import Dict

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

PLUGIN_DATA_DIR = Path("data", "plugins_data", "astrbot_plugin_api_caller")
PLUGIN_DATA_DIR.mkdir(parents=True, exist_ok=True)


@register(
    "astrbot_plugin_api_caller",
    "YourName",
    "天气查询 + 定时发送插件",
    "1.4.1",
)
class MyPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.api_url = getattr(config, "api_base_url", "https://api.nycnm.cn/API/weather.php")
        self.api_key = getattr(config, "api_key", "")
        self.default_format = getattr(config, "default_format", "image")
        tz_name = getattr(config, "timezone", "Asia/Shanghai")
        try:
            self.tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            logger.warning(f"[时区] 无效时区 '{tz_name}'，已回退为 Asia/Shanghai")
            self.tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        self.scheduled_tasks: Dict[str, dict] = {}
        self.task_counter = 0
        self.tasks_file = str(PLUGIN_DATA_DIR / "tasks.json")

    def _now(self) -> datetime.datetime:
        """获取当前时区的时间"""
        return datetime.datetime.now(self.tz)

    async def initialize(self):
        logger.info("天气API插件已加载")
        await self._load_tasks()

    # ══════════════════════════════════════════
    # URL 构建
    # ══════════════════════════════════════════

    def _build_url(self, city: str, days: int = None, fmt: str = "text") -> str:
        q = quote(city)
        url = f"{self.api_url}?query={q}&format={fmt}"
        if days and days >= 2:
            url += f"&action=forecast&days={days}"
        if self.api_key:
            url += f"&apikey={self.api_key}"
        return url

    # ══════════════════════════════════════════
    # 工具方法：调用 API
    # ══════════════════════════════════════════

    async def _query_weather_text(self, city: str, days: int = None) -> str:
        try:
            url = self._build_url(city, days, fmt="text")
            logger.info(f"[天气text] 请求URL: {url}")
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        return await resp.text() or None
                    logger.error(f"[天气text] 状态码：{resp.status}")
                    return None
        except asyncio.TimeoutError:
            logger.error("[天气text] 请求超时")
            return None
        except Exception as e:
            logger.error(f"[天气text] 请求出错: {e}")
            return None

    async def _query_weather_image(self, city: str, days: int = None) -> str:
        try:
            url = self._build_url(city, days, fmt="image")
            logger.info(f"[天气img] 请求URL: {url}")
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        content_type = resp.headers.get("Content-Type", "")
                        data = await resp.read()
                        if not data:
                            return None
                        if "png" in content_type.lower():
                            suffix = ".png"
                        elif "jpeg" in content_type.lower() or "jpg" in content_type.lower():
                            suffix = ".jpg"
                        else:
                            suffix = ".img"
                        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
                        tmp.write(data)
                        tmp.close()
                        return tmp.name
                    logger.error(f"[天气img] 状态码：{resp.status}")
                    return None
        except asyncio.TimeoutError:
            logger.error("[天气img] 请求超时")
            return None
        except Exception as e:
            logger.error(f"[天气img] 请求出错: {e}")
            return None

    # ══════════════════════════════════════════
    # 解析参数工具
    # ══════════════════════════════════════════

    def _parse_args(self, message_str: str):
        parts = message_str.strip().split()
        if len(parts) < 2:
            return None, None
        city = parts[1]
        days = None
        if len(parts) >= 3:
            try:
                d = int(parts[2])
                if d >= 2:
                    days = d
            except Exception:
                days = None
        return city, days

    def _get_sender_id(self, event: AstrMessageEvent) -> str:
        try:
            return str(event.get_sender_id())
        except Exception:
            return "unknown"

    # ══════════════════════════════════════════
    # 统一发送逻辑
    # ══════════════════════════════════════════

    async def _send_weather(self, event: AstrMessageEvent, city: str, days: int, fmt: str):
        title = f"📍 {city}天气" if not days else f"📍 {city} {days}天天气预报"
        try:
            if fmt == "image":
                yield event.plain_result("⏳ 图片获取中...")
                image_path = await self._query_weather_image(city, days)
                if image_path:
                    yield event.image_result(image_path)
                    try:
                        os.unlink(image_path)
                    except Exception:
                        pass
                else:
                    text = await self._query_weather_text(city, days)
                    if text:
                        yield event.plain_result(f"⚠️ 图片获取失败，已切换文字模式\n\n{title}\n\n{text}")
                    else:
                        yield event.plain_result("❌ 查询失败或无数据")
            else:
                yield event.plain_result("⏳ 查询中...")
                text = await self._query_weather_text(city, days)
                if text:
                    yield event.plain_result(f"{title}\n\n{text}")
                else:
                    yield event.plain_result("❌ 查询失败或无数据")
        except Exception as e:
            logger.error(f"[_send_weather] {e}")
            yield event.plain_result(f"❌ 查询失败：{e}")

    # ══════════════════════════════════════════
    # 手动调用指令（所有人可用）
    # ══════════════════════════════════════════

    @filter.command("天气", alias={"天气查询", "查天气"})
    async def query_weather(self, event: AstrMessageEvent):
        city, days = self._parse_args(event.message_str)
        if not city:
            yield event.plain_result(
                "❌ 参数不足\n\n"
                "用法：/天气 <城市> [天数]\n"
                "示例：/天气 北京 或 /天气 北京 5"
            )
            return
        async for r in self._send_weather(event, city, days, self.default_format):
            yield r

    @filter.command("天气text", alias={"文字天气"})
    async def query_weather_text(self, event: AstrMessageEvent):
        city, days = self._parse_args(event.message_str)
        if not city:
            yield event.plain_result(
                "❌ 参数不足\n\n"
                "用法：/天气text <城市> [天数]\n"
                "示例：/天气text 北京 或 /天气text 北京 5"
            )
            return
        async for r in self._send_weather(event, city, days, "text"):
            yield r

    @filter.command("天气img", alias={"图片天气"})
    async def query_weather_image(self, event: AstrMessageEvent):
        city, days = self._parse_args(event.message_str)
        if not city:
            yield event.plain_result(
                "❌ 参数不足\n\n"
                "用法：/天气img <城市> [天数]\n"
                "示例：/天气img 北京 或 /天气img 北京 5"
            )
            return
        async for r in self._send_weather(event, city, days, "image"):
            yield r

    # ══════════════════════════════════════════
    # 帮助指令
    # ══════════════════════════════════════════

    @filter.command("天气帮助")
    async def help_cmd(self, event: AstrMessageEvent):
        yield event.plain_result(
            "📖 天气插件使用说明\n"
            "\n"
            "━━━━━━ 所有人可用 ━━━━━━\n"
            "🔹 /天气 <城市> [天数]      默认格式\n"
            "🔹 /天气text <城市> [天数]  强制文字\n"
            "🔹 /天气img <城市> [天数]   强制图片\n"
            "\n"
            "🔹 /定时 add <HH:MM> <text|image> <城市> [天数]\n"
            "   添加每日定时推送\n"
            "   例：/定时 add 08:00 image 北京\n"
            "   例：/定时 add 08:00 image 北京 5\n"
            "🔹 /定时 del <ID> [ID2...]  删除定时任务（支持批量）\n"
            "   例：/定时 del 1 2 3\n"
            "   例：/定时 del 1,2,3\n"
            "🔹 /定时 list   查看自己的定时任务\n"
            "\n"
            "━━━━━━ 管理员额外权限 ━━━━━━\n"
            "🔸 /定时 list   查看所有人的任务\n"
            "🔸 /定时 del    可删除任何人的任务\n"
            "\n"
            "━━━━━━ 使用示例 ━━━━━━\n"
            "查询今日天气：/天气 北京\n"
            "查询5天预报：/天气 北京 5\n"
            "强制图片天气：/天气img 上海\n"
            "每天08:00推送图片：/定时 add 08:00 image 北京\n"
            "每天08:00推送5天预报：/定时 add 08:00 image 北京 5"
        )

    # ══════════════════════════════════════════
    # 定时任务统一入口（所有人可用）
    # ══════════════════════════════════════════

    def _check_admin(self, event: AstrMessageEvent) -> bool:
        return event.is_admin()

    @filter.command("定时")
    async def schedule(self, event: AstrMessageEvent):
        args = event.message_str.strip().split()
        sub = args[1].lower() if len(args) >= 2 else ""

        if sub == "add":
            async for result in self._schedule_add(event, args[2:]):
                yield result
        elif sub == "del":
            async for result in self._schedule_del(event, args[2:]):
                yield result
        elif sub == "list":
            async for result in self._schedule_list(event):
                yield result
        else:
            yield event.plain_result(
                "📖 定时指令用法：\n"
                "  /定时 add <HH:MM> <text|image> <城市> [天数]\n"
                "  /定时 del <ID> [ID2...]\n"
                "  /定时 list"
            )

    # ══════════════════════════════════════════
    # 定时任务子命令
    # ══════════════════════════════════════════

    async def _schedule_add(self, event: AstrMessageEvent, args: list):
        if len(args) < 3:
            yield event.plain_result(
                "❌ 参数不足\n"
                "用法：/定时 add <HH:MM> <text|image> <城市> [天数]\n"
                "示例：/定时 add 08:00 image 北京\n"
                "示例：/定时 add 08:00 image 北京 5"
            )
            return

        value = args[0]
        api_type = args[1].lower()
        city = args[2]
        days = None
        if len(args) >= 4:
            try:
                d = int(args[3])
                if d >= 2:
                    days = d
            except Exception:
                days = None

        if api_type not in ("text", "image"):
            yield event.plain_result("❌ 类型必须是 text 或 image")
            return

        try:
            hour, minute = map(int, value.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                raise ValueError
        except (ValueError, AttributeError):
            yield event.plain_result("❌ 时间格式错误，请用 HH:MM，例如 08:00")
            return

        self.task_counter += 1
        task_id = str(self.task_counter)
        umo = event.unified_msg_origin
        sender_id = self._get_sender_id(event)

        task = asyncio.create_task(
            self._run_time_task(task_id, umo, hour, minute, api_type, city, days)
        )
        self.scheduled_tasks[task_id] = {
            "task": task,
            "info": {
                "mode": "time",
                "value": f"每天 {hour:02d}:{minute:02d}",
                "type": api_type,
                "keyword": city,
                "days": days,
                "umo": umo,
                "sender_id": sender_id,
            }
        }
        self._save_tasks()
        day_str = f"\n  天数：{days}天" if days else ""
        yield event.plain_result(
            f"✅ 定时任务已添加\n"
            f"  ID：{task_id}\n"
            f"  时间：每天 {hour:02d}:{minute:02d}\n"
            f"  类型：{api_type}\n"
            f"  城市：{city}{day_str}"
        )

    async def _schedule_del(self, event: AstrMessageEvent, args: list):
        if not args:
            yield event.plain_result(
                "❌ 请提供任务 ID\n"
                "用法：/定时 del <ID> [ID2...]\n"
                "示例：/定时 del 1 2 3 或 /定时 del 1,2,3"
            )
            return

        # 支持空格和逗号分隔
        raw = " ".join(args)
        task_ids = [tid.strip() for tid in raw.replace(",", " ").split() if tid.strip()]

        sender_id = self._get_sender_id(event)
        is_admin = self._check_admin(event)
        success = []
        failed = []

        for task_id in task_ids:
            if task_id not in self.scheduled_tasks:
                failed.append(f"  ID {task_id}：不存在")
                continue
            task_owner = self.scheduled_tasks[task_id]["info"].get("sender_id", "")
            if not is_admin and sender_id != task_owner:
                failed.append(f"  ID {task_id}：无权限")
                continue
            self.scheduled_tasks[task_id]["task"].cancel()
            del self.scheduled_tasks[task_id]
            success.append(task_id)

        if success:
            self._save_tasks()

        lines = []
        if success:
            lines.append(f"✅ 已删除任务：{', '.join(success)}")
        if failed:
            lines.append("❌ 以下任务删除失败：\n" + "\n".join(failed))
        yield event.plain_result("\n".join(lines))

    async def _schedule_list(self, event: AstrMessageEvent):
        if not self.scheduled_tasks:
            yield event.plain_result("📋 当前没有定时任务")
            return

        sender_id = self._get_sender_id(event)
        is_admin = self._check_admin(event)
        lines = ["📋 定时任务列表："]

        for tid, item in self.scheduled_tasks.items():
            info = item["info"]
            owner = info.get("sender_id", "unknown")
            if is_admin or owner == sender_id:
                day_str = f"\n  天数：{info['days']}天" if info.get("days") else ""
                owner_str = f"\n  添加者：{owner}" if is_admin else ""
                lines.append(
                    f"\n  ID：{tid}\n"
                    f"  时间：{info['value']}\n"
                    f"  类型：{info['type']}\n"
                    f"  城市：{info['keyword']}{day_str}{owner_str}"
                )

        if len(lines) == 1:
            yield event.plain_result("📋 你还没有定时任务")
        else:
            yield event.plain_result("\n".join(lines))

    # ══════════════════════════════════════════
    # 持久化
    # ══════════════════════════════════════════

    def _save_tasks(self):
        data = {}
        for tid, item in self.scheduled_tasks.items():
            data[tid] = item["info"]
        with open(self.tasks_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"[持久化] 已保存 {len(data)} 个定时任务")

    async def _load_tasks(self):
        if not os.path.exists(self.tasks_file):
            return
        try:
            with open(self.tasks_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for tid, info in data.items():
                self.task_counter = max(self.task_counter, int(tid))
                umo = info.get("umo", "")
                api_type = info.get("type", "text")
                keyword = info.get("keyword", "")
                days = info.get("days", None)
                mode = info.get("mode", "")

                if mode == "time":
                    time_str = info["value"].replace("每天 ", "")
                    hour, minute = map(int, time_str.split(":"))
                    task = asyncio.create_task(
                        self._run_time_task(tid, umo, hour, minute, api_type, keyword, days)
                    )
                else:
                    continue

                self.scheduled_tasks[tid] = {"task": task, "info": info}
            logger.info(f"[持久化] 已恢复 {len(self.scheduled_tasks)} 个定时任务")
        except Exception as e:
            logger.error(f"[持久化] 读取任务失败: {e}")

    # ══════════════════════════════════════════
    # 后台定时任务执行逻辑
    # ══════════════════════════════════════════

    async def _send_api_result(self, umo: str, api_type: str, city: str, days: int = None):
        from astrbot.api.event import MessageChain
        image_path = None
        try:
            if api_type == "text":
                text = await self._query_weather_text(city, days)
                if text:
                    title = f"📍 {city}天气" if not days else f"📍 {city} {days}天天气预报"
                    chain = MessageChain().message(f"{title}\n\n{text}")
                else:
                    chain = MessageChain().message("❌ 查询失败或无数据")
            else:
                image_path = await self._query_weather_image(city, days)
                if image_path:
                    chain = MessageChain().file_image(image_path)
                else:
                    text = await self._query_weather_text(city, days)
                    if text:
                        title = f"📍 {city}天气" if not days else f"📍 {city} {days}天天气预报"
                        chain = MessageChain().message(f"⚠️ 图片获取失败\n\n{title}\n\n{text}")
                    else:
                        chain = MessageChain().message("❌ 查询失败或无数据")
            await self.context.send_message(umo, chain)
        except Exception as e:
            logger.error(f"[定时发送] 出错: {e}")
        finally:
            if image_path and os.path.exists(image_path):
                try:
                    os.unlink(image_path)
                except Exception:
                    pass

    async def _run_time_task(self, task_id: str, umo: str, hour: int, minute: int, api_type: str, city: str, days: int = None):
        logger.info(f"[定时任务 {task_id}] 启动，每天 {hour:02d}:{minute:02d} 执行")
        try:
            while True:
                now = self._now()
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if target <= now:
                    target += datetime.timedelta(days=1)
                wait_sec = (target - now).total_seconds()
                logger.info(f"[定时任务 {task_id}] 距下次执行 {wait_sec:.0f} 秒")
                await asyncio.sleep(wait_sec)
                logger.info(f"[定时任务 {task_id}] 执行中...")
                await self._send_api_result(umo, api_type, city, days)
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
