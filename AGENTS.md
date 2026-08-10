# AGENTS.md

AstrBot 插件：百合会论坛助手。`main.py` 是 AstrBot 入口（继承 Star），业务逻辑在 `yamibo/` 服务包。

## 命令（PowerShell，仓库根目录）

```powershell
.venv\Scripts\python.exe -m pytest tests -v                          # 全部测试
.venv\Scripts\python.exe -m pytest tests/test_parser.py -k thread -v # 单个/过滤
.\.venv\Scripts\ruff.exe check main.py yamibo tests scripts          # lint（. 开头必须加 .\ 或 & 调用符，否则被当模块点加载）
```

- 环境：`uv venv --python 3.12 .venv` + `uv sync --all-groups`（pytest/pytest-asyncio/ruff 在 dev group）
- 冒烟（真实论坛，默认跳过）：`$env:YAMIBO_AUTH=...; $env:YAMIBO_SALTKEY=...; .venv\Scripts\python.exe scripts\smoke_test.py`
- 验证插件能被 AstrBot 加载：用 **AstrBot 的 venv**（`D:\Projects\AstrBot\.venv`，含 astrbot 依赖）import main.py；dev venv 里没有 astrbot
- **详细开发文档：`docs/dev/DEVELOPMENT.md`**（含反爬/WAF 完整原理、页面结构速查表、踩坑记录、扩展指南；**gitignore 不入库**，改动勿提交）

## 关键架构

- `main.py`：仅指令注册/任务启动/配置读取。**不要 import main.py 写单测**（依赖 astrbot 无法在 dev venv 导入）；可测逻辑放 `yamibo/utils.py`
- `yamibo/client.py`：单一常驻 aiohttp session + CookieJar（自动吸收 Set-Cookie 维护 sid）；收到 405 自动重解 WAF 挑战（`yamibo/waf.py` quickjs 求解器）；nox token 每 600s 预刷新
- `yamibo/parser.py`：纯 HTML 解析（bs4），全部有 fixture 单测
- `yamibo/subscriber.py`：KV 抽象（测试用 `InMemoryStore`，main.py 用 `_AstrBotKVStore` 包装 put_kv_data/get_kv_data，要求 AstrBot >= 4.9.2）
- `yamibo/scheduler.py`：定时循环；配置经 `config_get` 可调用对象注入，key 用点号路径（如 `"sign.time"`）
- 配置读取一律 `cfg_get(config, "login.auth")` 点号路径，禁止扁平 `config.get("auth")`；schema 在 `_conf_schema.json`（嵌套分组 + hint）

## 论坛反爬/解析坑（硬经验，勿重蹈）

- 反爬细节与详细原理见 **`docs/dev/DEVELOPMENT.md`**（含 WAF 挑战机制、shim 清单、token 生命周期）；该目录与 `docs/superpowers/` 均 gitignore 不入库
- WAF 单测用 `tests/fixtures/synthetic_challenge.js` 合成脚本（只模拟调用模式），真实验证走 smoke_test
- 签到状态只判 `.signbtn` 按钮区文本——全页搜「今日已打卡」会因排行表里他人记录假阳性
- 搜索结果容器是 `div#threadlist`（不是 `#searchresult`）
- 签到记录表 `table.dt.mtm` **无 `<tbody>`**，用 `find_all("tr")`
- 页面可能 GBK 编码：client 已做 utf-8→gbk fallback（`_read_text`），勿改回 `resp.text()`
- 帖子正文需 `_clean_padding`（Discuz 反灌水随机字符行，如 `" Y3 N- A8 V/ \+ @ F% ^% V`）
- 合并转发：每批必须单个 `Comp.Nodes(nodes=[...])`——chain 里放多个 Node 会被 aiocqhttp 适配器逐个发送
- img2pdf 必须 `rotation=img2pdf.Rotation.ifvalid`（论坛图 EXIF Orientation=0 否则报错）
- 楼层号解析用 `_safe_int`（空文本会 `int('')` 崩溃）
- 帖子列表输出统一走 `yamibo/utils.py::fmt_list`（编号/作者/时间/链接/截断）

## 约定

- 修改 `_conf_schema.json` 后需在 AstrBot WebUI 重载插件才重建配置；cookie 字段（auth/saltkey）**不要加 `invisible: true`**（会从 WebUI 消失）
- `main.py` 顶部必须保留 `sys.path.insert(0, 插件目录)`（否则 AstrBot 加载时 `import yamibo` 失败）
- 新指令：管理员功能（登录态个人数据：签到/提醒/cookie状态）必须加 `@filter.permission_type(filter.PermissionType.ADMIN)`，其余公开
- 版本号四处同步：`metadata.yaml` / `pyproject.toml` / `yamibo/__init__.py` / `README.md` 版本徽章
- 测试：Windows 下 `tests/conftest.py` 已设 WindowsSelectorEventLoopPolicy（aiohttp TestServer 必需，勿删）；ruff 规则 E/F/W/I/UP、line-length 120、tests/ 豁免 E501
- 用户文档：`README.md`（配置/指令）、`docs/FAQ.md`（常见问题）
