import re

from yamibo.parser import (
    extract_formhash,
    parse_forum_threads,
    parse_hot_homepage,
    parse_ranklist,
    parse_sign_status,
)

SIGN_UNSIGNED = """
<div class="signbtn"><a class="btna" href="plugin.php?id=zqlj_sign&amp;sign=55e7ab08">点击打卡</a></div>
"""

SIGN_SIGNED = """
<div class="signbtn"><a class="btna" href="plugin.php?id=zqlj_sign&amp;sign=55e7ab08">今日已打卡</a></div>
<table class="dt mtm"><tbody><tr><th>用户名</th><th>打卡等级</th><th>总天数</th><th>月天数</th><th>上次打卡时间</th><th>上次奖励</th><th>总奖励</th><th>今日状态</th></tr>
<tr><td><a href="space-uid-621168.html" target="_blank">fold1486</a></td><td>百合化神</td><td>193</td><td>5</td><td>2026-08-07 21:46:04</td><td>1对象</td><td>269对象</td><td><font color="green">今日已打卡</font></td></tr>
</tbody></table>
"""

HOT_HOMEPAGE = """
<div id="portal_block_52_content"><div class="module cl xl xl1">
<ul><li><em>2026-08-06</em><a href="thread-574663-1-1.html" title="大家能接受百合cp中出现霸凌情节吗">大家能接受百合cp中出现霸凌情节吗</a></li>
<li><em>2026-08-07</em><a href="thread-574670-1-1.html" title="好难受">好难受</a></li></ul>
</div></div>
"""

RANKLIST = """
<div class="tl">
<table cellspacing="0" cellpadding="0">
<tbody><tr class="th"><td class="icn">&nbsp;</td><th>主题</th><td class="frm">版块</td><td class="by">作者</td><td width="60">回复</td></tr></tbody>
<tbody>
<tr><td class="icn"><span class="ranks ranks_1">1</span></td><th><a href="thread-519989-1-1.html" target="_blank">中文百合漫画区漫画汇总</a></th>
<td class="frm"><a href="forum-30-1.html" class="xg1">中文百合漫画区</a></td>
<td class="by"><cite><a href="space-uid-165700.html" target="_blank">hongyuny</a></cite></td><td>138</td></tr>
<tr><td class="icn"><span class="ranks ranks_2">2</span></td><th><a href="thread-574614-1-1.html" target="_blank">大家吃泡面喝汤吗？</a></th>
<td class="frm"><a href="forum-33-1.html" class="xg1">海域區</a></td>
<td class="by"><cite><a href="space-uid-165701.html" target="_blank">snke</a></cite></td><td>78</td></tr>
</tbody></table>
</div>
"""

FORUM_LIST = """
<div id="threadlisttableid">
<tbody id="normalthread_574681"><tr>
<td class="icn"></td>
<th class="common"><a href="thread-574681-1-1.html" onclick="atarget(this)" class="s xst">［韩国漫画］韓漫女刻板印象介紹</a></th>
<td class="by"><cite><a href="space-uid-1.html">KISE</a></cite><em><span>2026-8-7 18:21</span></em></td>
</tr></tbody>
<tbody id="normalthread_574233"><tr>
<th class="common"><a href="thread-574233-1-1.html" class="s xst">【再见菈菈】</a></th>
<td class="by"><cite><a href="space-uid-2.html">crystar23</a></cite><em><span>2026-8-7 01:56</span></em></td>
</tr></tbody>
</div>
"""


def test_extract_formhash():
    assert extract_formhash('formhash=55e7ab08"') == "55e7ab08"


def test_extract_formhash_missing():
    assert extract_formhash("<html></html>") is None


def test_parse_sign_status_unsigned():
    st = parse_sign_status(SIGN_UNSIGNED)
    assert st.signed_today is False


def test_parse_sign_status_signed():
    st = parse_sign_status(SIGN_SIGNED)
    assert st.signed_today is True


def test_parse_hot_homepage():
    items = parse_hot_homepage(HOT_HOMEPAGE)
    assert len(items) == 2
    assert items[0].tid == 574663
    assert items[0].date == "2026-08-06"


def test_parse_ranklist():
    items = parse_ranklist(RANKLIST)
    assert len(items) == 2
    assert items[0].tid == 519989
    assert items[0].reply_count == 138
    assert items[1].author == "snke"


def test_parse_forum_threads():
    items = parse_forum_threads(FORUM_LIST)
    assert len(items) == 2
    assert items[0].tid == 574681
    assert items[0].title.startswith("［韩国漫画］")
    assert items[0].author == "KISE"
