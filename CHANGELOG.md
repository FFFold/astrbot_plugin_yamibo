# 更新日志

本项目遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 与 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.0] - 2026-08-08

首个正式版本：百合会论坛助手完整可用。

### 新增

- **自动签到**：每日固定时间（东八区，可配置）自动打卡，支持手动签到与补签式 force 参数
- **热帖推送**：定时轮询本周回帖排行（`misc.php?mod=ranklist`），按 tid+日期去重，多会话绑定推送
- **漫画/连载打包**：解析帖子全部图片（`zoomfile` 全尺寸直链），生成 PDF（img2pdf 无损合并）或 QQ 合并转发（每图一节点、100 节点分批）；支持页数/文件大小上限与超限回退
- **帖子订阅**：多对多订阅（一个会话多帖、一帖多会话，KV 持久化）；按「只看楼主」跟踪（`authorid` 视图），新楼层文本+图片统一推送；发送失败不推进游标、连续失败 3 次自动暂停
- **浏览与搜索**：版块帖子列表（热度/最新排序、简繁体名称解析、翻页）、全站搜索（带冷却）、帖子内容预览（标题/作者/正文摘要/前 3 图）
- **权限分级**：签到/签到状态/提醒/cookie状态 仅管理员（`PermissionType.ADMIN`），其余功能公开
- **WAF 挑战自动求解**：内置 quickjs 求解器执行挑战脚本（纯 Python、无需浏览器），token 定期预刷新、405 自动重解；`manual_nox_token` 配置兜底
- **配置分组化**：登录/签到/热帖推送/帖子订阅/漫画打包/频率与安全 六组，全部带 hint 说明；`comic.workdir` 支持容器共享目录
- **健壮性**：GBK/UTF-8 编码自动探测、请求 3 次退避重试、Discuz 反灌水乱码行清洗、隐藏内容跳过、输入校验与命令冷却

### 修复

- 签到状态误判：页面底部排行表中其他用户的「今日已打卡」造成假阳性（只判定 `.signbtn` 按钮区）
- 搜索结果解析：结果容器为 `div#threadlist` 而非 `#searchresult`，搜索功能完全失效
- 漫画打包崩溃：async generator 被 `await`（重构为返回 chain 列表）
- 图片 EXIF Orientation 无效导致 img2pdf 拒绝（`Rotation.ifvalid`）
- 帖子预览崩溃：空楼层号 `int('')`（防御式解析）
- 帖子预览时间为空：楼主楼层 `#authorposton` 无 `<span>`（整块文本 + 时间正则兜底）
- 版块名称匹配：简体输入静默回退错误版块（简繁体/简称别名 + 未知名称报错）
- 签到记录解析：`tb=my` 页面真实 HTML 无 `<tbody>`（改用 `find_all("tr")`）
- 插件目录未加入 `sys.path` 导致 `ModuleNotFoundError`
- 配置项 `invisible` 导致 cookie 配置项在 WebUI 不显示
- 列表输出格式混乱/超长（统一 `fmt_list`：编号、截断、作者、时间、链接、翻页提示）

### 变更

- 配置结构由扁平改为分组嵌套（`login.auth`、`comic.workdir` 等点号路径），旧配置需重填
- 热帖/版块/搜索列表输出统一格式

### 依赖

- `aiohttp`、`beautifulsoup4`、`quickjs`、`img2pdf`（Python >= 3.10，推荐 3.12）
