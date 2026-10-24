import asyncio
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import img2pdf
import jmcomic
import requests
from PIL import Image

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File
from astrbot.api.star import Context, Star, register


@register("astrbot_plugin_jmcomic", "Air", "下载漫画并发送 PDF 或临时下载链接", "2.2.1")
class JMComicPDFPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.base_mount_dir = Path(__file__).resolve().parents[2] / "jm_tmp"
        self.base_mount_dir.mkdir(parents=True, exist_ok=True)

    @filter.command("jm")
    async def fetch_comic(self, event: AstrMessageEvent, comic_id: str = ""):
        """下载指定 ID 的漫画，按插件配置发送 PDF 或下载链接。"""
        if event.get_group_id() and not self.config.get("allow_group", False):
            yield event.plain_result("当前未开放群聊使用，请私聊机器人。")
            return

        access_mode = self.config.get("access_mode", "仅管理员")
        if access_mode == "仅管理员" and not event.is_admin():
            yield event.plain_result("当前仅允许 AstrBot 管理员使用此指令。")
            return

        if not re.fullmatch(r"[0-9]+", comic_id):
            yield event.plain_result("用法：/jm 漫画ID，ID 必须是纯数字。")
            return

        delivery_mode = self.config.get("delivery_mode", "下载链接")
        try:
            pdf_path = self._find_cached_pdf(comic_id)
            if pdf_path is None:
                yield event.plain_result(f"正在下载 {comic_id} 并生成 PDF，请稍候。")
                pdf_path = await asyncio.to_thread(self._download_pdf, comic_id)
            else:
                yield event.plain_result("找到已生成的 PDF，正在发送。")

            if delivery_mode == "直接发送 PDF":
                if event.get_platform_name() == "aiocqhttp":
                    await self._send_onebot_pdf(event, pdf_path)
                    yield event.plain_result("PDF 已发送。")
                else:
                    yield event.chain_result(
                        [File(name=pdf_path.name, file=str(pdf_path))]
                    )
            else:
                yield event.plain_result("正在上传 PDF 并获取临时下载链接。")
                download_url = await asyncio.to_thread(self._upload_pdf, pdf_path)
                yield event.plain_result(f"下载地址：\n{download_url}")
        except Exception:
            logger.exception("JMComic 请求处理失败，漫画 ID：%s", comic_id)
            yield event.plain_result("处理失败，请管理员查看 AstrBot 日志。")

    async def _send_onebot_pdf(self, event: AstrMessageEvent, pdf_path: Path) -> None:
        group_id = event.get_group_id()
        target_key = "group_id" if group_id else "user_id"
        target_id = str(group_id or event.get_sender_id())

        # OneBot 协议端把 file 当作 URL/路径识别，含中文、日文、全角符号等
        # 非 ASCII 字符会报“识别URL失败”，故发送前统一转为 ASCII 安全文件名。
        send_path, send_name = self._prepare_onebot_file(pdf_path)
        payload = await File(name=send_name, file=str(send_path)).to_dict()
        file_source = payload["data"]["file"]
        # OneBot v11 要求 file 字段为 URL 编码后的路径（或 file:// 链接），
        # 中文、空格、括号等未编码会导致“识别URL失败”，故本地路径一律转义。
        if not file_source.lower().startswith(("http://", "https://")):
            file_source = quote(file_source, safe="/")
        params = {target_key: target_id, "file": file_source, "name": send_name}
        if self_id := event.get_self_id():
            params["self_id"] = str(self_id)
        action = "upload_group_file" if group_id else "upload_private_file"
        await event.bot.call_action(action, **params)

    def _prepare_onebot_file(self, pdf_path: Path) -> tuple[Path, str]:
        """返回可安全发送给 OneBot 的文件路径与文件名。"""
        safe_name = self._ascii_pdf_name(pdf_path.name)
        if safe_name == pdf_path.name:
            return pdf_path, safe_name
        safe_path = self.base_mount_dir / safe_name
        if not safe_path.is_file() or safe_path.stat().st_size == 0:
            import shutil
            from tempfile import NamedTemporaryFile

            with NamedTemporaryFile(
                suffix=".pdf", dir=self.base_mount_dir, delete=False
            ) as tmp:
                with pdf_path.open("rb") as src:
                    shutil.copyfileobj(src, tmp)
                tmp_path = Path(tmp.name)
            os.replace(tmp_path, safe_path)
        return safe_path, safe_name

    @staticmethod
    def _ascii_pdf_name(name: str) -> str:
        ascii_name = re.sub(r"[^A-Za-z0-9._\s()-]+", "_", name)
        ascii_name = re.sub(r"[\s]+", " ", ascii_name)
        return ascii_name.strip(" ._") or f"{int.from_bytes(os.urandom(4), 'big'):08x}.pdf"

    def _find_cached_pdf(self, comic_id: str) -> Path | None:
        plain_path = self.base_mount_dir / f"{comic_id}.pdf"
        if plain_path.is_file() and plain_path.stat().st_size > 0:
            return plain_path
        # 兼容之前直传版使用的“[ID] 标题.pdf”缓存。
        for path in sorted(self.base_mount_dir.iterdir()):
            if (
                path.name.startswith(f"[{comic_id}] ")
                and path.suffix.lower() == ".pdf"
                and path.is_file()
                and path.stat().st_size > 0
            ):
                return path
        return None

    @staticmethod
    def _image_sort_key(path: Path):
        return [
            int(part) if part.isdigit() else part
            for part in re.split(r"([0-9]+)", path.as_posix())
        ]

    def _download_pdf(self, comic_id: str) -> Path:
        option = jmcomic.JmOption.default()
        pdf_name = f"{comic_id}.pdf"
        try:
            album = option.build_jm_client().get_album_detail(comic_id)
            title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(album.title))
            title = self._ascii_pdf_name(title)[:100]
            if title:
                pdf_name = f"[{comic_id}] {title}.pdf"
        except Exception:
            logger.warning("未能取得漫画标题，将使用 ID 命名：%s", comic_id)

        final_path = self.base_mount_dir / pdf_name
        with tempfile.TemporaryDirectory(
            prefix=f"tmp_{comic_id}_", dir=self.base_mount_dir
        ) as temp_path:
            work_dir = Path(temp_path)
            option.dir_rule.base_dir = str(work_dir)
            jmcomic.download_album(comic_id, option)
            images = sorted(
                (
                    path
                    for path in work_dir.rglob("*")
                    if path.is_file()
                    and path.suffix.lower() in (".webp", ".jpg", ".jpeg", ".png")
                ),
                key=self._image_sort_key,
            )
            if not images:
                raise RuntimeError("下载完成，但没有找到图片")

            converted_dir = work_dir / "_converted"
            converted_dir.mkdir()
            converted_images = []
            for index, image_path in enumerate(images):
                converted_path = converted_dir / f"{index:06d}.jpg"
                with Image.open(image_path) as image:
                    with image.convert("RGB") as rgb_image:
                        rgb_image.save(converted_path, "JPEG", quality=95)
                converted_images.append(str(converted_path))

            # 先在临时目录完成写入，避免失败时留下被误认为可复用的半成品。
            pending_path = work_dir / "result.pdf"
            with pending_path.open("wb") as output:
                img2pdf.convert(converted_images, outputstream=output)
            if pending_path.stat().st_size == 0:
                raise RuntimeError("生成的 PDF 为空")
            os.replace(pending_path, final_path)
        return final_path

    @staticmethod
    def _upload_pdf(pdf_path: Path) -> str:
        # 只有显式选择“下载链接”时才向第三方服务上传。
        with pdf_path.open("rb") as pdf_file:
            response = requests.post(
                "https://tmpfiles.org/api/v1/upload",
                files={"file": (pdf_path.name, pdf_file, "application/pdf")},
                timeout=(10, 120),
            )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise RuntimeError("临时文件服务未返回上传成功结果")
        data = payload.get("data")
        raw_url = data.get("url") if isinstance(data, dict) else None
        if not isinstance(raw_url, str):
            raise RuntimeError("临时文件服务没有返回下载地址")
        parts = urlsplit(raw_url)
        if parts.scheme not in ("http", "https") or parts.netloc != "tmpfiles.org":
            raise RuntimeError("临时文件服务返回了非预期的地址")
        if not parts.path.strip("/"):
            raise RuntimeError("临时文件服务返回了空下载路径")
        path = parts.path if parts.path.startswith("/dl/") else "/dl" + parts.path
        return urlunsplit(("https", "tmpfiles.org", path, parts.query, ""))