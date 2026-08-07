# astrbot_plugin_yamibo

百合会论坛（https://bbs.yamibo.com）助手插件：自动签到、热帖推送、漫画/连载帖解析打包、帖子订阅更新推送、浏览与搜索。

## 配置

1. 浏览器登录百合会
2. DevTools → Application → Cookies → https://bbs.yamibo.com
3. 复制 `EeqY_2132_saltkey` 与 `EeqY_2132_auth` 填入插件配置（两个都是 httpOnly/敏感值，不会显示在界面上）
4. 可选：`sign_time` 签到时间、`hot_push_interval_min` 热帖轮询、`sub_check_interval_min` 订阅轮询

## 指令

- 管理员：`/yamibo 签到` | `签到状态` | `提醒` | `cookie状态`
- 公开：`/yamibo 热帖 [N]` | `版块 [fid|名称] [hot|new] [页]` | `搜索 关键词`
- 公开：`/yamibo 帖子 <tid|链接>` | `漫画 <tid|链接> [pdf|fwd]`
- 公开：`/yamibo 订阅 <tid|链接>` | `订阅列表` | `取消订阅 <tid>`
- 公开：`/yamibo 订阅热帖` | `取消热帖`

## 说明

- 订阅按「只看楼主」跟踪，楼主新楼层（文本+图片）整体推送；失败推送不推进游标，连续失败 3 次自动暂停
- 漫画打包：aiocqhttp 平台默认合并转发（每图一节点，100 节点分批），其余平台默认 PDF
- cookie 过期时：`/yamibo cookie状态` 检查，重新提取填入即可
- 自动签到与推送依赖插件运行环境持续在线（AstrBot 常驻）

## 开发

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv -r requirements.txt
uv pip install --python .venv pytest pytest-asyncio ruff
.venv\Scripts\python.exe -m pytest tests -v
```

真实论坛冒烟（需要有效 cookie，环境变量门控）：

```powershell
$env:YAMIBO_AUTH="..." ; $env:YAMIBO_SALTKEY="..." ; .venv\Scripts\python.exe scripts\smoke_test.py
```
