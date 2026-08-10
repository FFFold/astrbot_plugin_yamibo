import os
import struct
import time
import zipfile
from pathlib import Path

from yamibo.packager import (
    Packager,
    build_file_chain,
    build_forward_chains,
    build_forward_chunks,
    chunk_list,
    ensure_safe_filename,
)

FAKE_PATHS = [Path(f"p{i}.jpg") for i in range(250)]

IMG_URL = "https://bbs.yamibo.com/data/attachment/forum/202608/09/a.jpg"


class _StubComp:
    """astrbot.api.message_components 的测试桩。"""

    @staticmethod
    def Node(**kw):
        return ("node", kw)

    @staticmethod
    def Nodes(**kw):
        return ("nodes", kw)

    @staticmethod
    def Plain(text):
        return ("plain", text)

    class Image:
        @classmethod
        def fromFileSystem(cls, path):
            return ("img", path)

    @staticmethod
    def File(**kw):
        return ("file", kw)


class FakeResp:
    def __init__(self, status: int = 200, data: bytes = b"", exc: Exception | None = None):
        self.status = status
        self._data = data
        self._exc = exc

    async def __aenter__(self):
        if self._exc is not None:
            raise self._exc
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def read(self) -> bytes:
        return self._data


class FakeSession:
    """每 URL 一个响应队列，按调用顺序弹出。"""

    def __init__(self, mapping: dict | None = None):
        self._mapping = mapping or {}
        self.attempts: list[str] = []

    def get(self, url: str, **kwargs):
        self.attempts.append(url)
        queue = self._mapping.get(url)
        if not queue:
            return FakeResp(exc=RuntimeError(f"no route for {url}"))
        return queue.pop(0)


def test_chunk_list():
    assert chunk_list([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_build_forward_chunks():
    chunks = build_forward_chunks(FAKE_PATHS)
    assert len(chunks) == 3
    assert len(chunks[0]) == 100
    assert len(chunks[2]) == 50


def test_build_forward_chains_headers_and_nodes():
    files = [Path("p0.jpg"), Path("p1.jpg"), Path("p2.jpg")]
    chains = build_forward_chains(files, 574233, "标题", 10000, comp=_StubComp)
    assert len(chains) == 1
    nodes = chains[0][0][1]["nodes"]
    assert len(nodes) == 4  # 头部 + 3 图
    assert nodes[0][1]["name"].startswith("百合会-")
    assert nodes[0][1]["content"] == [
        ("plain", "【标题】\n原帖：https://bbs.yamibo.com/thread-574233-1-1.html")
    ]
    assert nodes[1][1]["content"] == [("img", "p0.jpg")]
    assert all(n[1]["uin"] == 10000 for n in nodes)


def test_build_forward_chains_chunking():
    chains = build_forward_chains(FAKE_PATHS, 1, "t", 10000, comp=_StubComp)
    assert len(chains) == 3
    assert len(chains[0][0][1]["nodes"]) == 100  # 99 图 + 1 头部
    assert len(chains[1][0][1]["nodes"]) == 100
    assert len(chains[2][0][1]["nodes"]) == 51


def test_build_forward_chains_empty_title_fallback():
    chains = build_forward_chains([Path("p0.jpg")], 7, "  ", 1, comp=_StubComp)
    nodes = chains[0][0][1]["nodes"]
    assert nodes[0][1]["name"].startswith("百合会-7")


def test_build_file_chain_pdf(tmp_path):
    p1 = tmp_path / "a.png"
    _make_png(p1, (255, 0, 0))
    out_chain, over = build_file_chain([p1], 574233, "标题", "pdf", 1024 * 1024, comp=_StubComp)
    assert over is False
    assert out_chain == [("file", {"file": str(tmp_path / "标题.pdf"), "name": "标题.pdf"})]
    assert (tmp_path / "标题.pdf").read_bytes().startswith(b"%PDF")


def test_build_file_chain_zip(tmp_path):
    p1 = tmp_path / "a.png"
    _make_png(p1, (255, 0, 0))
    out_chain, over = build_file_chain([p1], 574233, "标题", "zip", 1024 * 1024, comp=_StubComp)
    assert over is False
    assert zipfile.is_zipfile(tmp_path / "标题.zip")


def test_build_file_chain_over_size(tmp_path):
    p1 = tmp_path / "a.png"
    _make_png(p1, (255, 0, 0))
    out_chain, over = build_file_chain([p1], 574233, "标题", "pdf", 1, comp=_StubComp)
    assert over is True
    assert out_chain == []


def test_build_forward_chunks_reserve():
    # 首批预留 1 个槽位（头部节点），保证插入后每批仍 ≤ 100 节点
    chunks = build_forward_chunks(FAKE_PATHS, reserve=1)
    assert len(chunks) == 3
    assert len(chunks[0]) == 99
    assert len(chunks[1]) == 100
    assert len(chunks[2]) == 51
    # 图片数恰为整倍数时，首批 99 + 尾部 1，不产生 101 节点批次
    exact = build_forward_chunks(FAKE_PATHS[:100], reserve=1)
    assert len(exact) == 2
    assert len(exact[0]) == 99
    assert len(exact[1]) == 1


def test_ensure_safe_filename():
    assert ensure_safe_filename('a/b\\c:d"e*f?g<h>i|j') == "abcdefghij"
    assert ensure_safe_filename("正常标题") == "正常标题"


def _make_png(path: Path, color: tuple) -> None:
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    w = h = 100
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(color) * w
    raw = row * h
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(data)


def test_pdf_build(tmp_path):
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    _make_png(p1, (255, 0, 0))
    _make_png(p2, (0, 0, 255))
    out = Packager.build_pdf([p1, p2], tmp_path / "out.pdf")
    assert out.read_bytes().startswith(b"%PDF")


def test_zip_build(tmp_path):
    p1 = tmp_path / "a.png"
    _make_png(p1, (1, 2, 3))
    out = Packager.build_zip([p1], tmp_path / "out.zip")
    assert zipfile.is_zipfile(out)


def test_cleanup_older_than_missing_dir(tmp_path):
    Packager.cleanup_older_than(tmp_path / "nope", time.time())  # 目录不存在不抛异常


# ---- 下载：失败统计 / 重试 / 原子写 ----

async def test_download_success_no_tmp_left(tmp_path):
    sess = FakeSession({IMG_URL: [FakeResp(data=b"img-a")]})
    p = Packager(sess, workdir=tmp_path, concurrency=2)
    res = await p.download_images([IMG_URL], "c1")
    assert res.total == 1
    assert res.failed == 0
    assert len(res.files) == 1
    assert res.files[0].read_bytes() == b"img-a"
    assert not list((tmp_path / "c1").glob("*.tmp"))


async def test_download_failure_counted(tmp_path):
    bad = "https://bbs.yamibo.com/bad.jpg"
    sess = FakeSession({bad: [FakeResp(exc=RuntimeError("boom")), FakeResp(exc=RuntimeError("boom"))]})
    p = Packager(sess, workdir=tmp_path)
    res = await p.download_images([bad], "c2")
    assert res.failed == 1
    assert res.files == []
    assert not list((tmp_path / "c2").glob("*.tmp"))  # 失败后无 .tmp 残留


async def test_download_retries_once_on_exception(tmp_path):
    url = "https://bbs.yamibo.com/flaky.jpg"
    sess = FakeSession({url: [FakeResp(exc=RuntimeError("net")), FakeResp(data=b"retried")]})
    p = Packager(sess, workdir=tmp_path)
    res = await p.download_images([url], "c3")
    assert res.failed == 0
    assert res.files[0].read_bytes() == b"retried"
    assert len(sess.attempts) == 2


async def test_download_http_error_not_retried(tmp_path):
    url = "https://bbs.yamibo.com/404.jpg"
    sess = FakeSession({url: [FakeResp(status=404), FakeResp(data=b"x")]})
    p = Packager(sess, workdir=tmp_path)
    res = await p.download_images([url], "c4")
    assert res.failed == 1
    assert len(sess.attempts) == 1


async def test_download_reuses_complete_file(tmp_path):
    sess = FakeSession({IMG_URL: [FakeResp(data=b"img-a")]})
    p = Packager(sess, workdir=tmp_path, concurrency=2)
    await p.download_images([IMG_URL], "c5")
    # 远端不可用时，已完成的本地文件直接复用、不重新请求
    p2 = Packager(FakeSession({}), workdir=tmp_path, concurrency=2)
    res = await p2.download_images([IMG_URL], "c5")
    assert res.failed == 0
    assert len(res.files) == 1


async def test_download_empty_input(tmp_path):
    p = Packager(FakeSession({}), workdir=tmp_path)
    res = await p.download_images([], "c6")
    assert res.total == 0
    assert res.failed == 0
    assert res.files == []


def test_cleanup_older_than_keeps_new_files(tmp_path):
    d = tmp_path / "dl"
    d.mkdir()
    old = d / "old.jpg"
    old.write_bytes(b"x")
    os.utime(old, (time.time() - 100, time.time() - 100))
    # 快照取当前时间并留 100ms 余量，规避 Windows mtime 量化导致的时间竞态
    cutoff = time.time() - 0.1
    time.sleep(0.02)
    # 快照之后写入的文件不应被删除
    new = d / "new.jpg"
    new.write_bytes(b"y")
    Packager.cleanup_older_than(d, cutoff)
    assert not old.exists()
    assert new.exists()
    assert d.exists()  # 目录非空时保留
    os.utime(new, (time.time() - 100, time.time() - 100))
    Packager.cleanup_older_than(d, cutoff)
    assert not d.exists()
