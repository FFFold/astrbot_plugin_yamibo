import re

from yamibo.parser import (
    extract_formhash,
    parse_forum_threads,
    parse_hot_homepage,
    parse_my_records,
    parse_ranklist,
    parse_search_results,
    parse_sign_status,
    parse_thread,
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


THREAD_PAGE = """
<div id="postlist">
<div id="post_1001">
<table><tr>
<td><div id="favatar1001" class="pls"><div class="authi"><a href="space-uid-731857.html" target="_blank">crystar23</a></div></div></td>
<td><div id="postnum1001"><em>1</em></div>
<div id="authorposton1001"><span>2026-7-27 19:46</span></div>
<div id="postmessage_1001" class="t_f">
请支持我们小狗🐟和茉里！
<img src="data/attachment/forum/202607/27/194631h9ufu5spp2fxn16p.jpg" zoomfile="data/attachment/forum/202607/27/194631h9ufu5spp2fxn16p.jpg" class="zoom">
</div></td></tr></table>
</div>
<div id="post_1002">
<table><tr>
<td><div id="favatar1002" class="pls"><div class="authi"><a href="space-uid-9999.html" target="_blank">路人甲</a></div></div></td>
<td><div id="postnum1002"><em>2</em></div>
<div id="authorposton1002"><span>2026-7-27 20:00</span></div>
<div id="postmessage_1002" class="t_f">好耶</div></td></tr></table>
</div>
</div>
"""

THREAD_HIDDEN = """
<div id="postlist"><div id="post_1001">
<table><tr><td><div id="favatar1001" class="pls"><div class="authi"><a href="space-uid-731857.html">crystar23</a></div></div></td>
<td><div id="postnum1001"><em>1</em></div><div id="authorposton1001"><span>2026-7-27 19:46</span></div>
<div id="postmessage_1001" class="t_f">
<blockquote class="locked"><p>本帖隐藏的内容需要回复才可以浏览</p></blockquote>
公开内容
</div></td></tr></table></div></div>
"""

THREAD_NO_LOGIN = """<title>提示信息 - 百合会 - Powered by Discuz!</title><div class="alert_error">您需要登录</div>"""


def test_parse_thread_floors():
    tc = parse_thread(THREAD_PAGE, tid=574233)
    assert len(tc.floors) == 2
    f0 = tc.floors[0]
    assert f0.is_op is True
    assert f0.author_uid == 731857
    assert f0.floor == 1
    assert f0.images == ["https://bbs.yamibo.com/data/attachment/forum/202607/27/194631h9ufu5spp2fxn16p.jpg"]
    assert f0.text.startswith("请支持我们小狗")
    assert tc.floors[1].is_op is False


def test_parse_thread_hidden_content_skipped():
    tc = parse_thread(THREAD_HIDDEN, tid=1, skip_hidden=True)
    assert "隐藏的内容" not in tc.floors[0].text
    assert "公开内容" in tc.floors[0].text


def test_parse_thread_not_logged_in():
    tc = parse_thread(THREAD_NO_LOGIN, tid=1)
    assert len(tc.floors) == 0


SEARCH_RESULT = """
<div id="searchresult">
<li class="pbw" id="519989">
<h3 class="xs3"><a href="forum.php?mod=viewthread&amp;tid=519989&amp;highlight=百合" target="_blank">中文<strong>百合</strong>漫画区汇总</a></h3>
<p class="xg1">138 个回复 - 119 次查看</p>
<p>图源：浮沫fumo</p>
<p><span>2026-8-7 17:00</span></p>
</li>
<li class="pbw" id="574689">
<h3 class="xs3"><a href="forum.php?mod=viewthread&amp;tid=574689&amp;highlight=百合" target="_blank">【百合吧汉化组】Baby On Board</a></h3>
<p class="xg1">1 个回复 - 74 次查看</p>
</li>
</div>
"""

MY_RECORDS = """
<table class="dt mtm">
<tbody><tr><th width="130">打卡时间</th><th>打卡固定奖励</th><th>打卡前N名奖励</th><th>连续打卡奖励</th><th>用户组额外奖励</th><th>节日额外奖励</th></tr>
<tr><td>2026-08-07 21:46:04</td><td>1对象</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
<tr><td>2026-08-05 15:02:07</td><td>1对象</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
</tbody></table>
"""


def test_parse_search_results():
    items = parse_search_results(SEARCH_RESULT)
    assert len(items) == 2
    assert items[0].tid == 519989
    assert items[0].reply_count == 138
    assert items[1].title.startswith("【百合吧汉化组】")


def test_parse_my_records():
    rec = parse_my_records(MY_RECORDS)
    assert rec["last_time"] == "2026-08-07 21:46:04"
    assert rec["last_reward"] == "1对象"
    assert rec["count"] == 2
