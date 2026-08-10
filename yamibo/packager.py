"""图片下载与打包：PDF 合并、合并转发节点分批、ZIP。"""

import asyncio
import re
import zipfile
from pathlib import Path

import img2pdf

FORWARD_CHUNK = 100
SAFE_NAME_RE = re.compile(r'[\\/:*?"<>|]')


def ensure_safe_filename(name: str) -> str:
    return SAFE_NAME_RE.sub("", name).strip() or "untitled"


def chunk_list(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def build_forward_chunks(files: list[Path], *, reserve: int = 0) -> list[list[Path]]:
    """按 FORWARD_CHUNK 分块；首批预留 reserve 个槽位（如用于插入头部节点），
    保证首批插入头部后总节点数仍 ≤ FORWARD_CHUNK。"""
    if not files:
        return []
    first, rest = files[: FORWARD_CHUNK - reserve], files[FORWARD_CHUNK - reserve :]
    return [first] + chunk_list(rest, FORWARD_CHUNK)


class Packager:
    """下载图片并构建 PDF/ZIP。发送与清理由调用方完成。"""

    def __init__(self, session, *, concurrency: int = 4, workdir: Path | None = None) -> None:
        self._session = session
        self._concurrency = concurrency
        self._workdir = workdir or Path(".")

    async def download_images(
        self, urls: list[str], prefix: str, *, referer: str = ""
    ) -> list[Path]:
        """并发下载图片到 workdir/prefix/，返回文件路径列表（按输入顺序）。"""
        if not urls:
            return []
        out_dir = self._workdir / ensure_safe_filename(prefix)
        out_dir.mkdir(parents=True, exist_ok=True)
        sem = asyncio.Semaphore(self._concurrency)

        async def one(index: int, url: str) -> Path | None:
            ext = Path(url.split("?", 1)[0]).suffix or ".jpg"
            dest = out_dir / f"{index:04d}{ext}"
            if dest.exists() and dest.stat().st_size > 0:
                return dest
            headers = {"Referer": referer} if referer else {}
            async with sem:
                try:
                    async with self._session.get(url, headers=headers, timeout=30) as resp:
                        if resp.status != 200:
                            return None
                        data = await resp.read()
                    dest.write_bytes(data)
                    return dest
                except Exception:
                    return None

        results = await asyncio.gather(*(one(i, u) for i, u in enumerate(urls)))
        return [r for r in results if r is not None]

    @staticmethod
    def build_pdf(files: list[Path], out: Path) -> Path:
        # 部分论坛图片 EXIF Orientation 为无效值(0)，用 ifvalid 忽略
        out.write_bytes(
            img2pdf.convert([str(f) for f in files], rotation=img2pdf.Rotation.ifvalid)
        )
        return out

    @staticmethod
    def build_zip(files: list[Path], out: Path) -> Path:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as zf:
            for i, f in enumerate(files):
                zf.write(f, f"{i:04d}{f.suffix}")
        return out

    @staticmethod
    def cleanup(*paths: Path) -> None:
        for p in paths:
            try:
                if p.is_dir():
                    for child in p.iterdir():
                        child.unlink(missing_ok=True)
                    p.rmdir()
                else:
                    p.unlink(missing_ok=True)
            except OSError:
                pass
