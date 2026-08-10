import struct
import zipfile
from pathlib import Path

from yamibo.packager import (
    Packager,
    build_forward_chunks,
    chunk_list,
    ensure_safe_filename,
)

FAKE_PATHS = [Path(f"p{i}.jpg") for i in range(250)]


def test_chunk_list():
    assert chunk_list([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_build_forward_chunks():
    chunks = build_forward_chunks(FAKE_PATHS)
    assert len(chunks) == 3
    assert len(chunks[0]) == 100
    assert len(chunks[2]) == 50


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


def test_cleanup(tmp_path):
    d = tmp_path / "dl"
    d.mkdir()
    (d / "x.jpg").write_bytes(b"x")
    Packager.cleanup(d)
    assert not d.exists()
