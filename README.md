# AstrBot 上海交通大学医学院课表插件

基于 [Playwright](https://playwright.dev/) 抓取上海交通大学医学院教务系统的课表信息，支持通过命令、LLM 工具以及其他插件调用查询。

## 简介

`astrbot_plugin_shsmu_schedule` 是一个 AstrBot 插件。它会自动登录交医教务系统，打开「我的课表」，并根据你的查询返回指定日期的课程安排（时间、课程名、教室）。

登录、翻页、解析全部自动化，无需手动操作浏览器。

## 功能特性

- 🔐 **自动登录**：使用插件配置中的学号/密码自动登录教务系统
- 📅 **最近有课查询**：从今天开始，自动向后查找最近一个「有课」的日子（最多 30 天）
- 🎯 **指定日期查询**：支持查询「N 天后」的课表
- 🤖 **LLM 工具集成**：注册了 `get_schedule` 工具，AI 可以主动调用来回答「明天有课吗」这类问题
- 🔌 **插件接口**：提供 `get_schedule_skill` / `get_schedule_text_skill` 方法，供其他插件调用
- 🛠 **健壮登录**：兼容多种登录页面结构（CSS 选择器 / label / placeholder / 回车提交），失败时自动保存 HTML 供排查
- 📦 **自动装内核**：首次运行时若缺少 Chromium，会自动执行 `playwright install chromium`

## 前置要求

- AstrBot 已部署并运行
- Python 3.9+
- 依赖：
  - `astrbot`（由 AstrBot 提供）
  - `playwright`

## 安装

1. 将本插件文件夹放入 AstrBot 的插件目录（通常为 `data/plugins/`）：

```
data/plugins/astrbot_plugin_shsmu_schedule/
├── main.py
├── metadata.yaml
└── ...
```

2. 安装依赖：

```bash
pip install playwright
```

3. 安装浏览器内核（可选，插件首次运行时会自动安装）：

```bash
playwright install chromium
```

4. 重启 AstrBot，插件会自动加载。

## 配置

在 AstrBot 的插件配置面板（或插件的 `config.json`）中填写：

| 配置项 | 类型 | 说明 |
|--------|------|------|
| `username` | string | 教务系统学号 |
| `password` | string | 教务系统密码 |

配置读取顺序：插件配置 → AstrBot 全局配置 → 插件目录下的 `config.json`。

> ⚠️ 密码会以明文形式保存在配置中，请注意保护你的 AstrBot 部署环境。

## 使用

### 命令

| 命令 | 说明 |
|------|------|
| `/课表` | 查询最近有课的一天（今天有课则返回今天） |
| `/课表 3` | 查询 3 天后的课表 |
| `/课表 7` | 查询一周后的课表 |

返回格式示例：

```
📅 2025-05-20 周二

⏰ 08:00-09:40
📚 系统解剖学
🏫 东区教学楼 201

⏰ 10:00-11:40
📚 生理学
🏫 西区教学楼 105
```

若当天无课而未来有课，会返回：

```
当天没有课，最近的课程在明天（2025-05-21 周三），有——

⏰ 08:00-09:40
📚 系统解剖学
🏫 东区教学楼 201
```

### LLM 工具

插件注册了名为 `get_schedule` 的 LLM 工具，参数：

| 参数 | 类型 | 说明 |
|------|------|------|
| `days` | int | `0` 表示查找最近有课的一天；`>0` 表示查询具体某天（1=明天，7=一周后） |

接入 LLM 后可直接对话：「明天有课吗？」「下周一上什么课？」等。

### 供其他插件调用

```python
# 获取结构化数据
schedule = await plugin.get_schedule_skill(mode="next")
schedule = await plugin.get_schedule_skill(mode="offset", offset_days=3)

# 获取格式化文本
text = await plugin.get_schedule_text_skill(mode="next")
```

`get_schedule_skill` 返回的字典结构：

```python
{
    "date": "2025-05-20",       # 日期
    "weekday": "周二",           # 星期
    "class_info": "...",         # 班级信息
    "courses": [
        {"time": "08:00-09:40", "course": "系统解剖学", "classroom": "东区 201"},
        ...
    ],
    "offset_days": 1,            # 相对今天的偏移（mode=next/offset 时）
    "mode": "next",              # "next" 或 "offset"
    "note": "..."                # 可选，提示信息
}
```

## 工作流程

1. 启动（或复用）无头 Chromium 浏览器
2. 打开 `https://jwstu.shsmu.edu.cn/` 登录页
3. 尝试多种选择器填写学号/密码并提交，若已登录则跳过
4. 等待教务主页的 `#divKeBiao` 元素出现
5. 点击「我的课表」，切换到新弹窗
6. 在课表页切换到「日视图」，点击「今天」按钮
7. 按需向后翻页，读取 `th.fc-day-header[data-date]` 和 `a.paikeStyle` 课程卡片
8. 解析并格式化返回

调试用的 `page_dump.html` 和 `login_page_dump.html` 会保存在插件目录下。

## 文件说明

| 文件 | 说明 |
|------|------|
| `main.py` | 插件主逻辑 |
| `auth_state.json` | （可选）Playwright 登录状态文件，存在时会被加载 |
| `config.json` | （可选）本地配置 |
| `page_dump.html` | 课表页调试快照（自动生成） |
| `login_page_dump.html` | 登录页调试快照（登录失败时生成） |

## 常见问题

**Q：提示「找不到登录输入框」怎么办？**
A：请查看插件目录下的 `login_page_dump.html`，确认页面结构是否变化（如新增验证码、跳转 SSO 等）。教务系统改版后可能需要更新 `_do_login` 中的选择器列表。

**Q：首次运行很慢？**
A：首次运行若缺少 Chromium 内核，插件会自动执行 `playwright install chromium`，需要下载约 100MB 的浏览器，请耐心等待。

**Q：为什么每次都要重新登录？**
A：当前版本会读取 `auth_state.json`（如果存在），但尚未实现自动保存登录状态。可手动用 Playwright 生成该文件，或等待后续版本。

**Q：课表解析不全 / 时间格式异常？**
A：课表卡片解析依赖 `时间 / 课程 / 教室` 三个字段前缀。若教务系统改版导致格式变化，请检查 `_parse_card_text`。

**Q：能查历史课表吗？**
A：目前只支持「今天及以后」。若要查询过去日期，需修改 `_goto_day_and_extract` 使用 `fc-prev-button`。

## 免责声明

- 本插件仅用于个人学习与课程查询，请勿用于自动化刷课、恶意请求等用途。
- 使用本插件即表示你已知晓并同意：登录凭据将保存在你的 AstrBot 配置中，请自行做好安全防护。
- 教务系统如有改版导致插件失效，欢迎提交 Issue 或 PR。

## License

MIT