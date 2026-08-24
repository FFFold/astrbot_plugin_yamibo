<div align="center">

# 🌸 百合会论坛助手

为 [AstrBot](https://astrbot.app) 打造的百合会论坛（[bbs.yamibo.com](https://bbs.yamibo.com)）自动化插件

自动签到 · 热帖推送 · 漫画打包 · 订阅追踪 · 浏览搜索

<!-- 版本徽章与 metadata.yaml / pyproject.toml / yamibo/__init__.py 同步 -->
![版本](https://img.shields.io/badge/version-0.2.4-blue?style=flat-square)
![AstrBot](https://img.shields.io/badge/AstrBot-%3E%3D4.9.2-8a2be2?style=flat-square)
![Python](https://img.shields.io/badge/Python-3.10%2B-2e8b57?style=flat-square)
![License](https://img.shields.io/badge/License-MIT-orange?style=flat-square)

</div>

<p align="center">
<b>⭐ 如果这个项目对你有帮助，欢迎 <a href="https://github.com/FFFold/astrbot_plugin_yamibo">Star</a> 支持！</b>
</p>

---

## 📑 目录

- [✨ 功能一览](#功能一览)
- [🚀 快速开始](#快速开始)
- [⚙️ 配置](#配置)
- [💬 指令](#指令)
- [📌 说明与限制](#说明与限制)
- [❓ 常见问题](#常见问题)
- [🛠️ 开发](#开发)
- [📄 更新日志](#更新日志)
- [📜 License](#license)

## ✨ 功能一览

| 功能 | 说明 |
|---|---|
| ✅ **自动签到** | 每天固定时间自动打卡（东八区），支持手动签到与状态查询 |
| 🔥 **热帖推送** | 今日热度榜双通道推送：每天定时全量日报 + 榜单刷新后增量推新进榜帖（自动对齐约 5h 的榜单缓存） |
| 📚 **漫画/连载打包** | 解析帖子全部图片，生成 PDF 或 QQ 合并转发（每图一节点） |
| 🔔 **帖子订阅** | 按「只看楼主」跟踪，楼主新楼层（文本+图片）以合并转发整体推送 + 一条更新通知；非转发平台自动回退单条发送 |
| 🔍 **浏览与搜索** | 版块帖子列表（热度/最新排序）、全站搜索、帖子内容预览 |
| 💬 **未读提醒** | 查询账号未读消息数量（管理员） |

## 🚀 快速开始

1. 克隆到 AstrBot 插件目录：

```bash
cd AstrBot/data/plugins
git clone https://github.com/FFFold/astrbot_plugin_yamibo.git
```

2. 在 AstrBot 的 Python 环境中安装依赖（通过插件市场安装会自动处理）：

```bash
cd AstrBot/data/plugins/astrbot_plugin_yamibo
pip install -r requirements.txt
```

3. 重载插件，填写配置（见下）。

> 💡 **提示**：登录 cookie 是本插件的唯一必需配置，其余项均有合理默认值，开箱即用。

## ⚙️ 配置

### 1. 登录认证（必需）

在配置面板的「论坛登录认证」组填写：

1. 浏览器登录百合会
2. 打开 DevTools → Application → Cookies → `https://bbs.yamibo.com`
3. 复制 `EeqY_2132_auth` 与 `EeqY_2132_saltkey` 两个值填入配置

> ⚠️ `saltkey` 是 httpOnly cookie，在控制台执行 `document.cookie` 取不到，**必须**从 DevTools 的 Cookies 面板复制。该 cookie 对插件是必需的（Discuz 校验登录态的盐）。

### 2. 其他配置组

| 分组 | 配置项 | 默认 | 说明 |
|---|---|---|---|
| 论坛登录认证 | user_agent / proxy / manual_nox_token | 内置 UA / 空 / 空 | 可选：自定义 UA、代理，以及 WAF token 手动兜底 |
| 签到 | enable / time | `true` / `10:00` | 自动签到开关与时间（HH:MM，东八区） |
| 热帖推送 | enable / count / daily.enable / daily.time / incremental.enable / incremental.interval_min | `true` / `10` / `true` / `20:00` / `true` / `60` | 全量日报（每天定时推完整今日热度榜）+ 增量雷达（榜单刷新后推新进榜帖，自动对齐约 5h 的榜单缓存；interval_min 仅作解析失败兜底） |
| 帖子订阅 | check_interval_min / text_max_len / image_max | `30` / `2000` / `50` | 订阅轮询间隔、文本截断、单楼层图片上限（图片随推送真实发送） |
| 漫画打包 | deliver_mode / max_pages / max_file_size_mb / download_concurrency / cleanup_delay_min / workdir | `auto` / `300` / `45` / `4` / `10` / `""` | 发送方式（auto/pdf/fwd/zip）、页数与体积上限、并发、临时文件延迟清理（分钟）、临时目录 |
| 频率与安全 | comic_cooldown_sec / search_cooldown_sec / skip_hidden_content / notify_auth_fail | `60` / `30` / `true` / `false` | 冷却与隐私开关 |

> ⏱️ **定时任务抖动**：全量日报与自动签到在设定时间后 **0~5 分钟随机延迟**执行（避免每天同一时刻产生规律性请求）。如设定 20:30，实际推送可能在 20:30~20:35 之间。

> 🐳 **容器部署提示**：AstrBot 与协议端（如 NapCat）分容器部署时，`comic.workdir` 填两者共享的卷目录（如 `/data/shared/yamibo`），协议端才能读取生成的 PDF 文件。

## 💬 指令

命令组 `/yamibo`（参数用空格分隔，`<tid|链接>` 支持帖子 ID 或完整链接）：

### 👑 管理员专用（操作登录态账号个人数据）

| 指令 | 说明 |
|---|---|
| `/yamibo 签到 [force]` | 手动签到（`force` 跳过"已签到"检查） |
| `/yamibo 签到状态` | 今日状态、最近打卡时间与奖励、记录条数 |
| `/yamibo 提醒` | 未读提醒数量 |
| `/yamibo cookie状态` | 校验 cookie 是否有效（返回 uid，不泄露 cookie 值） |

### 🌐 公开（所有用户）

| 指令 | 说明 |
|---|---|
| `/yamibo 热帖 [N]` | 今日热度榜，N 默认 10，最大 30 |
| `/yamibo 订阅热帖` / `取消热帖` | 绑定/解绑本会话为热帖推送目标 |
| `/yamibo 版块 <fid\|名称> [hot\|new] [页]` | 版块帖子列表，名称支持简繁体（如 `动漫区`/`動漫區`） |
| `/yamibo 搜索 <关键词>` | 全站搜索（带冷却） |
| `/yamibo 帖子 <tid\|链接>` | 帖子内容预览：标题/作者/正文摘要/前 3 张图 |
| `/yamibo 漫画 <tid\|链接> [pdf\|fwd\|zip]` | 打包发送（默认按配置；`pdf` 强制 PDF，`fwd` 强制合并转发，`zip` 强制 ZIP） |
| `/yamibo 订阅 <tid\|链接>` | 订阅帖子更新（只看楼主，从当前最新楼层开始跟踪） |
| `/yamibo 订阅列表` | 查看本会话订阅 |
| `/yamibo 取消订阅 <tid>` | 取消订阅 |
| `/yamibo 帮助` | 指令说明与 cookie 获取教程 |

### 📝 使用示例

```text
/yamibo 搜索 恋人不行
/yamibo 版块 贴图区 hot 2
/yamibo 帖子 https://bbs.yamibo.com/thread-574233-1-1.html
/yamibo 漫画 574233
/yamibo 订阅 574597
```

## 📌 说明与限制

- **订阅机制**：按「只看楼主」跟踪（Discuz `authorid` 视图）。aiocqhttp 平台将楼主新楼层按每 100 层一批打包为 QQ 合并转发（每楼层一个节点，节点含文本+原帖链接+图片），随后发送一条无链接的简短更新通知（`【标题】楼主 更新了 L10-L12`，连续楼层合并区间）；其他平台自动回退：全部新楼层按批（≤100 楼）合并直发，合并文本超字符预算（8000 字）自动拆批。通知只发给内容送达成功的会话。图片上限见 `subscription.image_max`；仅当一个批次至少一个目标送达才推进游标，全员失败（或部分批次未送达）保留游标下轮重试，连续失败 3 次自动暂停（`订阅列表` 可见），恢复更新后自动解除
- **漫画发送**：aiocqhttp 平台默认合并转发（首条为标题+原帖链接，随后每图一节点、100 节点分批、批间 2 秒）；其余平台默认 PDF；也可用 `zip` 模式打包；PDF/ZIP 超过 `max_file_size_mb` 提示无法发送；下载失败的图片会提示数量（单张自动重试 1 次）
- **登录态维护**：插件会自动求解 WAF 挑战并定期刷新会话；cookie 过期时执行 `/yamibo cookie状态` 检测，重新提取填入即可（可选配置 `manual_nox_token` 兜底）
- **隐私**：签到/提醒等个人数据仅管理员可用；隐藏内容（`[hide]`/购买）默认跳过不推送
- **运行前提**：自动签到与推送依赖 AstrBot 常驻在线

## ❓ 常见问题

见 [docs/FAQ.md](docs/FAQ.md)（cookie 过期、订阅不推送、PDF 发送失败等）。

## 🛠️ 开发

```bash
uv venv --python 3.12 .venv
uv sync --all-groups
.venv\Scripts\python.exe -m pytest tests -v
.venv\Scripts\ruff.exe check main.py yamibo tests scripts
```

真实论坛冒烟（需要有效 cookie，环境变量门控）：

```powershell
$env:YAMIBO_AUTH="..." ; $env:YAMIBO_SALTKEY="..." ; .venv\Scripts\python.exe scripts\smoke_test.py
```

## 📄 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

## 📜 License

[GNU Affero General Public License v3.0](LICENSE) © Fold

---

<div align="center">

如果这个插件对你有帮助，欢迎 ⭐ Star、提交 [Issue](https://github.com/FFFold/astrbot_plugin_yamibo/issues) 或 PR！

</div>
