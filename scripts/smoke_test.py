r"""真实论坛冒烟测试（需要有效 cookie，默认不运行）。

用法（PowerShell）：
  $env:YAMIBO_AUTH="..." ; $env:YAMIBO_SALTKEY="..." ; .venv\Scripts\python.exe scripts\smoke_test.py
"""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from yamibo.client import ForumClient  # noqa: E402
from yamibo.parser import parse_sign_status, parse_thread  # noqa: E402


async def main() -> None:
    auth = os.environ.get("YAMIBO_AUTH", "")
    saltkey = os.environ.get("YAMIBO_SALTKEY", "")
    if not auth or not saltkey:
        print("SKIP: 需要设置 YAMIBO_AUTH 与 YAMIBO_SALTKEY")
        return
    client = ForumClient(
        auth=auth, saltkey=saltkey,
        user_agent=os.environ.get(
            "YAMIBO_UA",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        ),
    )
    await client.start()
    try:
        html = await client.get_text("/forum.php")
        assert client.is_logged_in(html), "登录态检查失败"
        print("OK: 登录态正常")
        html2 = await client.get_text("/forum.php?mod=viewthread&tid=574233&ordertype=1")
        tc = parse_thread(html2, 574233)
        assert tc.floors, "帖子解析为空"
        print(f"OK: 帖子解析 {len(tc.floors)} 楼，主楼图片 {len(tc.op_images())} 张")
        html3 = await client.get_text("/plugin.php?id=zqlj_sign")
        st = parse_sign_status(html3)
        print(f"OK: 签到状态 signed_today={st.signed_today}")
        html4 = await client.get_text("/forum.php?mod=viewthread&tid=574233&authorid=731857&ordertype=1")
        tc4 = parse_thread(html4, 574233)
        print(f"OK: 只看楼主视图 {len(tc4.floors)} 楼")
        hot = await client.get_hot_threads(5)
        print(f"OK: 热帖 {len(hot)} 条，第一条: {hot[0].title[:30] if hot else 'N/A'}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
