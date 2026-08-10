"""图片下载与打包：PDF 合并、合并转发节点分批、ZIP。"""

import asyncio
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import img2pdf

from yamibo.utils import fmt_comic_header

FORWARD_CHUNK = 100
SAFE_NAME_RE = re.compile(r'[\\/:*?"<>|]')


@dataclass
class DownloadResult:
    """图片下载结果：files 为成功文件（按输入顺序），failed 为失败张数。"""

    files: list[Path] = field(default_factory=list)
    total: int = 0
    failed: int = 0


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


def build_forward_chains(
    files: list[Path], tid_num: int, title: str, self_id: int, *, comp=None
) -> list[list]:
    """生成合并转发节点批次（每批 100 节点，首条为标题 + 原帖链接）。

    每条 chain 必须是单个 Comp.Nodes（内含全部节点），aiocqhttp 适配器对
    chain 中的每个 Node/Nodes 段分别发送一次转发。
    comp 为 astrbot message_components 模块（可注入测试桩，缺省惰性导入）。
    """
    if comp is None:
        import astrbot.api.message_components as comp

    chunks = build_forward_chunks(files, reserve=1)
    title_clean = (title or "").strip()
    sender_name = f"百合会-{title_clean[:20]}" if title_clean else f"百合会-{tid_num}"
    chains: list[list] = []
    for i, chunk in enumerate(chunks):
        nodes = [
            comp.Node(uin=self_id, name=sender_name, content=[comp.Image.fromFileSystem(str(f))])
            for f in chunk
        ]
        if i == 0:
            nodes.insert(
                0,
                comp.Node(
                    uin=self_id,
                    name=sender_name,
                    content=[comp.Plain(fmt_comic_header(title, tid_num))],
                ),
            )
        chains.append([comp.Nodes(nodes=nodes)])
    return chains


def build_file_chain(
    files: list[Path], tid_num: int, title: str, kind: str, max_bytes: int, *, comp=None
) -> tuple[list, bool]:
    """生成 PDF/ZIP 文件消息链。kind: "pdf" | "zip"。

    返回 (chain 组件列表, 是否超限)。max_bytes <= 0 表示不限制。
    comp 为 astrbot message_components 模块（可注入测试桩，缺省惰性导入）。
    """
    if comp is None:
        import astrbot.api.message_components as comp

    ext = "zip" if kind == "zip" else "pdf"
    out = files[0].parent / f"{ensure_safe_filename(title or str(tid_num))}.{ext}"
    if kind == "zip":
        Packager.build_zip(files, out)
    else:
        Packager.build_pdf(files, out)
    if max_bytes > 0 and out.stat().st_size > max_bytes:
        return [], True
    return [comp.File(file=str(out), name=out.name)], False


class Packager:
    """下载图片并构建 PDF/ZIP。发送与清理由调用方完成。"""

    def __init__(self, session, *, concurrency: int = 4, workdir: Path | None = None) -> None:
        self._session = session
        self._concurrency = concurrency
        self._workdir = workdir or Path(".")

    async def download_images(
        self, urls: list[str], prefix: str, *, referer: str = ""
    ) -> DownloadResult:
        """并发下载图片到 workdir/prefix/，返回 DownloadResult。

        单张失败重试 1 次（仅网络异常，非 200 不重试）；先写 .tmp 再原子 rename，
        崩溃残留的半截文件不会以正式文件名出现，也不会被复用。
        """
        if not urls:
            return DownloadResult(total=0)
        out_dir = self._workdir / ensure_safe_filename(prefix)
        out_dir.mkdir(parents=True, exist_ok=True)
        sem = asyncio.Semaphore(self._concurrency)

        async def one(index: int, url: str) -> Path | None:
            ext = Path(url.split("?", 1)[0]).suffix or ".jpg"
            dest = out_dir / f"{index:04d}{ext}"
            if dest.exists() and dest.stat().st_size > 0:
                return dest
            headers = {"Referer": referer} if referer else {}
            tmp = out_dir / f"{index:04d}{ext}.tmp"
            for _ in range(2):  # 网络异常重试 1 次
                async with sem:
                    try:
                        async with self._session.get(url, headers=headers, timeout=30) as resp:
                            if resp.status != 200:
                                return None
                            data = await resp.read()
                        tmp.write_bytes(data)
                        os.replace(tmp, dest)
                        return dest
                    except Exception:
                        tmp.unlink(missing_ok=True)
                        continue
            return None

        results = await asyncio.gather(*(one(i, u) for i, u in enumerate(urls)))
        files = [r for r in results if r is not None]
        return DownloadResult(files=files, total=len(urls), failed=len(urls) - len(files))

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
    def cleanup_older_than(directory: Path, cutoff: float) -> None:
        """删除目录中 mtime <= cutoff 的文件（含 .tmp 残留），清空后移除目录。

        用于发送后的延迟清理：只删快照时间点前的文件，避免误删并发/重启后新下载的文件。
        """
        try:
            for p in directory.iterdir():
                try:
                    if p.is_file() and p.stat().st_mtime <= cutoff:
                        p.unlink(missing_ok=True)
                except OSError:
                    continue
            directory.rmdir()
        except OSError:
            pass
