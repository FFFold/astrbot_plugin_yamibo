# 常见问题（FAQ）

## cookie 相关

### 插件提示「cookie 无效或已过期」怎么办？

1. 执行 `/yamibo cookie状态` 确认
2. 浏览器重新登录百合会
3. DevTools → Application → Cookies → `https://bbs.yamibo.com`
4. 复制新的 `EeqY_2132_auth` 与 `EeqY_2132_saltkey` 填入插件配置并重载

> `EeqY_2132_saltkey` 是 httpOnly cookie，**必须**从 DevTools 的 Cookies 面板复制；在控制台执行 `document.cookie` 只能拿到 `auth`。

### 为什么需要两个 cookie？只要 auth 不行吗？

Discuz 的登录态校验依赖 `auth + saltkey` 的组合。只填 `auth` 会导致页面返回未登录（`discuz_uid=0`）。

### WAF 挑战求解失败（提示 405 / WAF challenge）

插件内置求解器自动刷新 WAF token（`nox_jst_v1`），一般无需干预。如果持续失败：

1. 在浏览器中打开一次论坛首页（刷新 WAF 会话）
2. 把浏览器 Cookie 中的 `nox_jst_v1` 值填入配置 `login.manual_nox_token` 兜底
3. 若仍失败，可能是 WAF 升级，请等待插件更新或提 issue

## 订阅相关

### 订阅的帖子很久没推送了？

- 订阅按「只看楼主」跟踪——只有**楼主**发新楼层才会推送，网友讨论楼层不推送
- 连续 3 次推送失败（如目标会话被禁言）会自动暂停，`/yamibo 订阅列表` 会显示「已暂停」；楼主下次更新时会自动恢复
- 检查 `/yamibo cookie状态`，cookie 失效时轮询无法进行

### 订阅后会把历史楼层都推给我吗？

不会。订阅时以楼主当前最新楼层为基线，只跟踪之后的更新。

## 漫画/打包相关

### 合并转发（fwd）发送失败或没有反应？

- 合并转发依赖协议端支持 `send_forward_msg`（NapCat、Lagrange 等均支持）
- 每批最多 100 节点，大漫画会分批发送（批间 2 秒）
- 非 aiocqhttp 平台请用 PDF 模式（`/yamibo 漫画 <tid> pdf`）

### PDF 太大发不出去？

超过配置 `comic.max_file_size_mb`（默认 45MB，考虑 Telegram 50MB 限制）会自动回退为逐张发送前 20 张图片。可调低 `max_pages` 限制页数。

### 容器部署时协议端读不到文件？

AstrBot 与协议端分容器部署时，把 `comic.workdir` 配置为两者**共享卷**的目录（如 `/data/shared/yamibo`），PDF/图片会写入该目录。

## 其他

### 搜索提示「搜索失败或触发论坛频率限制」

Discuz 搜索有风控。默认 30 秒冷却，若仍频繁触发，调大 `limits.search_cooldown_sec`。

### 自动签到没生效？

- 确认 `sign.enable` 为 true、`sign.time` 正确（东八区）
- 签到循环每 60 秒检查一次时间，修改配置后无需重启
- 检查 `/yamibo 签到状态` 是否显示已签到（该功能曾存在误判 bug，请更新到最新版本）

### 管理员指令提示无权限？

管理员指令（签到/提醒/cookie状态等）需要发送者的平台权限为管理员（群管理员/群主或机器人所有者）。普通用户不可用——这是刻意的隐私隔离设计。

### 正文里出现奇怪符号？

老版本会把 Discuz 反灌水随机字符显示出来；更新到 0.1.0 及以上版本会自动清洗。

### 插件的临时文件存在哪里？

默认 `data/plugin_data/astrbot_plugin_yamibo/`，发送成功后自动清理；配置 `comic.workdir` 可自定义。

### 支持哪些平台？

所有 AstrBot 支持的平台均可使用文本功能；QQ 合并转发仅 aiocqhttp（NapCat/Lagrange 等），其余平台自动使用 PDF。
