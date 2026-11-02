import os
import re
import sys
import json
import asyncio
from pathlib import Path

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger

from playwright.async_api import async_playwright

PLUGIN_DIR = Path(__file__).parent
STATE_FILE = PLUGIN_DIR / "auth_state.json"


class ShsmuSchedulePlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self.playwright = None
        self.browser = None
        self._install_lock = asyncio.Lock()
        logger.info("=== ShsmuSchedulePlugin v3.1 已加载（去掉 networkidle）===")

    # ============================================================
    # 浏览器
    # ============================================================
    async def _ensure_playwright_browser(self, event=None):
        async with self._install_lock:
            if self.browser is not None:
                return
            try:
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                    ],
                )
                return
            except Exception as e:
                if "Executable doesn't exist" not in str(e):
                    raise

            logger.info("Playwright 内核缺失，正在安装…")
            if event is not None:
                try:
                    await event.send(event.plain_result("先下载浏览器内核，稍等两分钟喵…"))
                except Exception as send_err:
                    logger.warning(f"发送提示失败: {send_err}")

            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "playwright", "install", "chromium",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"Playwright 内核安装失败: {stderr.decode(errors='ignore')}")

            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                ],
            )

    async def _ensure_browser(self, event=None):
        if self.browser is None:
            await self._ensure_playwright_browser(event)
        return self.browser

    # ============================================================
    # 配置
    # ============================================================
    def _get_config_value(self, key: str, default: str = "") -> str:
        if self.config is not None:
            try:
                val = self.config.get(key, default)
                if val:
                    return val
            except Exception:
                pass
        try:
            cfg = self.context.get_config()
            if cfg:
                val = cfg.get(key, default)
                if val:
                    return val
        except Exception:
            pass
        local_cfg = PLUGIN_DIR / "config.json"
        if local_cfg.exists():
            try:
                with open(local_cfg, "r", encoding="utf-8") as f:
                    val = json.load(f).get(key, default)
                    if val:
                        return val
            except Exception:
                pass
        return default

    # ============================================================
    # 健壮登录
    # ============================================================
    async def _do_login(self, page, username: str, password: str):
        """兼容多种页面结构的登录。失败时保存 login_page_dump.html 供排查。"""
        try:
            await page.wait_for_selector("#divKeBiao", timeout=5000)
            logger.info("检测到已登录，跳过登录步骤")
            return
        except Exception:
            pass

        logger.info(f"当前 URL: {page.url}")

        # 1. 用户名
        username_candidates = [
            'input[name="username"]',
            'input[name="j_username"]',
            'input[name="loginName"]',
            'input#username',
            'input#j_username',
            'input[type="text"]',
            'input[type="email"]',
        ]
        filled_user = False
        for sel in username_candidates:
            try:
                await page.wait_for_selector(sel, timeout=5000, state="visible")
                await page.fill(sel, username)
                logger.info(f"✓ 用户名填入成功，选择器: {sel}")
                filled_user = True
                break
            except Exception:
                continue

        if not filled_user:
            for kw in ("Username", "用户名", "账号", "学号"):
                try:
                    await page.get_by_label(kw).fill(username, timeout=3000)
                    logger.info(f"✓ 用户名填入成功，label: {kw}")
                    filled_user = True
                    break
                except Exception:
                    continue

        if not filled_user:
            for kw in ("请输入账号", "请输入用户名", "Username", "学号"):
                try:
                    await page.get_by_placeholder(kw).fill(username, timeout=3000)
                    logger.info(f"✓ 用户名填入成功，placeholder: {kw}")
                    filled_user = True
                    break
                except Exception:
                    continue

        # 2. 密码
        password_candidates = [
            'input[name="password"]',
            'input[name="j_password"]',
            'input#password',
            'input#j_password',
            'input[type="password"]',
        ]
        filled_pwd = False
        for sel in password_candidates:
            try:
                await page.wait_for_selector(sel, timeout=5000, state="visible")
                await page.fill(sel, password)
                logger.info(f"✓ 密码填入成功，选择器: {sel}")
                filled_pwd = True
                break
            except Exception:
                continue

        # 3. 失败时保存 HTML
        if not (filled_user and filled_pwd):
            try:
                html = await page.content()
                (PLUGIN_DIR / "login_page_dump.html").write_text(html, encoding="utf-8")
                logger.error(
                    f"登录页 HTML 已保存到 login_page_dump.html，当前 URL: {page.url}"
                )
            except Exception as e:
                logger.warning(f"保存登录页失败: {e}")
            raise RuntimeError(
                f"找不到登录输入框。当前 URL: {page.url}。"
                f"请查看插件目录下的 login_page_dump.html 排查。"
            )

        # 4. 登录按钮
        button_candidates = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Sign in")',
            'button:has-text("Login")',
            'button:has-text("登录")',
            'button:has-text("登 录")',
            'a:has-text("登录")',
        ]
        clicked = False
        for sel in button_candidates:
            try:
                await page.click(sel, timeout=3000)
                logger.info(f"✓ 登录按钮点击成功，选择器: {sel}")
                clicked = True
                break
            except Exception:
                continue

        if not clicked:
            try:
                await page.keyboard.press("Enter")
                logger.info("✓ 用回车键提交登录")
                clicked = True
            except Exception:
                pass

        if not clicked:
            raise RuntimeError("找不到登录按钮，也未成功按回车提交")

    # ============================================================
    # 解析
    # ============================================================
    @staticmethod
    def _parse_card_text(text: str) -> dict:
        field_map = {"时间": "time", "课程": "course", "教室": "classroom"}
        result = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            for cn, en in field_map.items():
                matched = False
                for sep in ("：", ":"):
                    prefix = cn + sep
                    if line.startswith(prefix):
                        result[en] = line[len(prefix):].strip()
                        matched = True
                        break
                if matched:
                    break
        return result

    async def _extract_day_schedule(self, page) -> dict:
        date_str, weekday = None, None
        try:
            header = page.locator("th.fc-day-header[data-date]").first
            date_str = await header.get_attribute("data-date")
            weekday = (await header.locator("span").inner_text()).strip()
        except Exception as e:
            logger.warning(f"读取日期失败: {e}")

        class_info = ""
        try:
            class_info = (await page.locator("#ClassInfo").inner_text()).strip()
        except Exception:
            pass

        courses = []
        try:
            cards = page.locator("a.paikeStyle")
            count = await cards.count()
            for i in range(count):
                text = await cards.nth(i).inner_text()
                parsed = self._parse_card_text(text)
                if parsed:
                    courses.append(parsed)
        except Exception as e:
            logger.warning(f"读取课程卡片失败: {e}")

        return {
            "date": date_str,
            "weekday": weekday,
            "class_info": class_info,
            "courses": courses,
        }

    # ============================================================
    # 翻页
    # ============================================================
    async def _switch_to_day_view_and_today(self, page, event=None):
        """切到日视图并回到今天。已去掉 networkidle，改用元素等待。"""

        # 1. 切到日视图（尝试多种可能的类名）
        day_view_selectors = [
            "button.fc-agendaDay-button",
            "button.fc-timeGridDay-button",
            "a.fc-agendaDay-button",
            ".fc-agendaDay-button",
            "button:has-text('日')",
            "button:has-text('Day')",
        ]
        clicked_day = False
        for sel in day_view_selectors:
            try:
                loc = page.locator(sel).first
                await loc.wait_for(state="visible", timeout=3000)
                await loc.click(timeout=3000)
                logger.info(f"✓ 已切换到日视图，选择器: {sel}")
                clicked_day = True
                break
            except Exception:
                continue

        if not clicked_day:
            logger.warning("未找到日视图按钮，按日视图逻辑继续执行")

        # 2. 等日历头部出现（替代 networkidle）
        try:
            await page.wait_for_selector(
                "th.fc-day-header[data-date]",
                state="attached",
                timeout=8000,
            )
        except Exception as e:
            logger.warning(f"等待日历头部超时: {e}")

        await page.wait_for_timeout(500)

        # 3. 回到今天
        try:
            today_btn = page.locator("button.fc-today-button").first
            await today_btn.click(timeout=5000)
            await page.wait_for_timeout(800)
        except Exception as e:
            logger.warning(f"点『今天』失败: {e}")

        # 4. 确认视图：日视图下 day-header 应该只有 1 个
        try:
            cnt = await page.locator("th.fc-day-header[data-date]").count()
            logger.info(f"✓ 当前日历头部数量 = {cnt}（日视图应为 1）")
        except Exception:
            pass

        # 5. 命令模式才发提示，AI 模式不发
        if event is not None:
            try:
                await event.send(event.plain_result("哼哼，进来了！让本小姐翻翻看课表……"))
            except Exception as e:
                logger.warning(f"发送进度提示失败: {e}")

    async def _find_next_schedule_day(self, page, max_days: int = 30, event=None) -> dict:
        await self._switch_to_day_view_and_today(page, event)

        last_schedule = None
        for i in range(max_days):
            schedule = await self._extract_day_schedule(page)
            last_schedule = schedule
            if schedule["courses"]:
                if i > 0:
                    schedule["offset_days"] = i
                schedule["mode"] = "next"
                return schedule

            logger.info(f"[翻页 {i}] {schedule.get('date')} 无课，查看下一天…")

            # 翻页：去掉 networkidle
            try:
                await page.locator("button.fc-next-button").first.click(timeout=5000)
            except Exception as e:
                logger.warning(f"点击下一天失败: {e}")
                break

            await page.wait_for_timeout(600)
            # 用日历头部出现代替 networkidle
            try:
                await page.wait_for_selector(
                    "th.fc-day-header[data-date]",
                    state="attached",
                    timeout=5000,
                )
            except Exception:
                pass

        if last_schedule is None:
            last_schedule = {"date": None, "weekday": None, "class_info": "", "courses": []}
        last_schedule["note"] = f"未来 {max_days} 天内都没有课程哦"
        last_schedule["mode"] = "next"
        return last_schedule

    async def _goto_day_and_extract(self, page, offset_days: int, event=None) -> dict:
        await self._switch_to_day_view_and_today(page, event)

        for i in range(offset_days):
            try:
                await page.locator("button.fc-next-button").first.click(timeout=5000)
            except Exception as e:
                logger.warning(f"点击下一天失败: {e}")
                break

            await page.wait_for_timeout(600)
            try:
                await page.wait_for_selector(
                    "th.fc-day-header[data-date]",
                    state="attached",
                    timeout=5000,
                )
            except Exception:
                pass

        schedule = await self._extract_day_schedule(page)
        schedule["offset_days"] = offset_days
        schedule["mode"] = "offset"
        if not schedule["courses"]:
            schedule["note"] = "咦？这天没有课哦~"
        return schedule

    # ============================================================
    # 核心抓取
    # ============================================================
    async def _fetch_schedule(
        self,
        event=None,
        mode: str = "next",
        offset_days: int = 0,
        max_days: int = 30,
    ) -> dict:
        browser = await self._ensure_browser(event)

        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            storage_state=str(STATE_FILE) if STATE_FILE.exists() else None,
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)
        page = await context.new_page()

        username = self._get_config_value("username")
        password = self._get_config_value("password")
        if not username or not password:
            await context.close()
            raise ValueError("请先在插件配置面板填写学号和密码")

        try:
            # 1. 打开登录页
            logger.info("打开登录页…")
            await page.goto(
                "https://jwstu.shsmu.edu.cn/",
                wait_until="domcontentloaded",
                timeout=90000,
            )
            logger.info(f"已到达: {page.url}")

            # 2. 登录
            await self._do_login(page, username, password)

            # 3. 等主页
            logger.info("等待教务主页加载…")
            await page.wait_for_selector("#divKeBiao", timeout=90000)
            logger.info("已进入教务主页")

            # 4. 点"我的课表"
            logger.info("点击『我的课表』…")
            async with page.expect_popup(timeout=30000) as popup_info:
                await page.locator("#divKeBiao").get_by_text("我的课表").click(timeout=15000)
            page2 = await popup_info.value

            # 5. 等日历（去掉 networkidle，用 domcontentloaded + 元素等待）
            await page2.wait_for_load_state("domcontentloaded", timeout=60000)
            await page2.wait_for_selector("button.fc-agendaDay-button", timeout=60000)
            await page2.wait_for_timeout(2000)
            logger.info("课表页已就绪，开始翻页…")

            # 6. 取数据
            if mode == "offset" and offset_days > 0:
                schedule = await self._goto_day_and_extract(page2, offset_days, event)
            else:
                schedule = await self._find_next_schedule_day(page2, max_days=max_days, event=event)

            # 保存 HTML
            try:
                html = await page2.content()
                (PLUGIN_DIR / "page_dump.html").write_text(html, encoding="utf-8")
                logger.info("页面 HTML 已保存到 page_dump.html")
            except Exception as e:
                logger.warning(f"保存 HTML 失败: {e}")

            return schedule

        finally:
            await context.close()

    # ============================================================
    # Skill 接口（供其他插件调用）
    # ============================================================
    async def get_schedule_skill(
        self,
        event: AstrMessageEvent = None,
        mode: str = "next",
        offset_days: int = 0,
    ) -> dict:
        """供其他插件调用。返回结构化字典。"""
        return await self._fetch_schedule(event, mode=mode, offset_days=offset_days)

    async def get_schedule_text_skill(
        self,
        event: AstrMessageEvent = None,
        mode: str = "next",
        offset_days: int = 0,
    ) -> str:
        """供其他插件调用。返回人类可读文本。"""
        schedule = await self._fetch_schedule(event, mode=mode, offset_days=offset_days)
        return self.format_schedule(schedule)

    # ============================================================
    # 格式化
    # ============================================================
    @staticmethod
    def format_schedule(schedule: dict) -> str:
        if schedule.get("note") and not schedule.get("courses"):
            return schedule["note"]

        courses = schedule.get("courses") or []
        date = schedule.get("date") or "?"
        weekday = schedule.get("weekday") or ""
        offset = schedule.get("offset_days")
        mode = schedule.get("mode")

        lines = []

        # ── 情况一：当天没课，最近课程在 n 天后 ──
        if mode == "next" and offset and offset > 0:
            if offset == 1:
                day_desc = "明天"
            elif offset == 2:
                day_desc = "后天"
            else:
                day_desc = f"{offset} 天后"

            lines.append(
                f"当天没有课，最近的课程在{day_desc}"
                f"（{date} {weekday}），有——"
            )
            lines.append("")
            for c in courses:
                lines.append(f"⏰ {c.get('time', '时间未知')}")
                lines.append(f"📚 {c.get('course', '课程未知')}")
                lines.append(f"🏫 {c.get('classroom', '教室未知')}")
                lines.append("")
            return "\n".join(lines).rstrip()

        # ── 情况二：查询指定某天（mode == offset） ──
        if mode == "offset":
            title = f"📅 {date} {weekday}（{offset} 天后）"
        # ── 情况三：今天就有课（mode == next 且 offset 为 0/None） ──
        else:
            title = f"📅 {date} {weekday}"
        lines.append(title)

        if not courses:
            lines.append("")
            lines.append("这一天没有课哦～")
            return "\n".join(lines).rstrip()

        lines.append("")
        for c in courses:
            lines.append(f"⏰ {c.get('time', '时间未知')}")
            lines.append(f"📚 {c.get('course', '课程未知')}")
            lines.append(f"🏫 {c.get('classroom', '教室未知')}")
            lines.append("")

        return "\n".join(lines).rstrip()

    # ============================================================
    # LLM 工具
    # ============================================================
    @filter.llm_tool(name="get_schedule")
    async def get_schedule_tool(self, event: AstrMessageEvent, days: int = 0) -> str:
        """获取上海交通大学医学院教务系统的课表信息。

        Args:
            days (int): 从今天往后偏移的天数。0 表示查找最近有课的一天；大于 0 表示查询具体某天（如 1 = 明天，7 = 一周后）。
        """
        if days and days > 0:
            schedule = await self._fetch_schedule(
                event, mode="offset", offset_days=int(days)
            )
        else:
            schedule = await self._fetch_schedule(event, mode="next")

        logger.info(f"课表数据: {json.dumps(schedule, ensure_ascii=False)}")
        return self.format_schedule(schedule)

    # ============================================================
    # 命令
    # ============================================================
    @filter.command("课表")
    async def get_schedule_cmd(self, event: AstrMessageEvent):
        """用法：/课表 或 /课表 3（3 天后）"""
        yield event.plain_result("正在获取课表，请稍候喵…")

        offset_days = 0
        m = re.search(r"\d+", event.message_str or "")
        if m:
            try:
                offset_days = int(m.group())
            except ValueError:
                offset_days = 0

        try:
            if offset_days > 0:
                schedule = await self._fetch_schedule(
                    event, mode="offset", offset_days=offset_days
                )
            else:
                schedule = await self._fetch_schedule(event, mode="next")

            logger.info(f"课表数据: {json.dumps(schedule, ensure_ascii=False)}")
            text = self.format_schedule(schedule)
            if len(text) > 3000:
                text = text[:3000] + "\n…（内容过长已截断）"
        except Exception as e:
            logger.error(f"获取课表失败: {e}")
            text = f"呜呜，获取课表失败了……{e}"

        yield event.plain_result(text)

    # ============================================================
    # 生命周期
    # ============================================================
    async def terminate(self):
        try:
            if self.browser:
                await self.browser.close()
        except Exception as e:
            logger.warning(f"关闭浏览器失败: {e}")
        try:
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            logger.warning(f"停止 Playwright 失败: {e}")