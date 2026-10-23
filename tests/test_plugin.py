"""Run in a Python environment with AstrBot and requirements.txt installed."""

import atexit
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pikepdf
import requests
from PIL import Image

# AstrBot 导入时会创建默认目录，将它们限制在测试临时目录内。
_RUNTIME_TEMP = tempfile.TemporaryDirectory()
os.environ["ASTRBOT_ROOT"] = _RUNTIME_TEMP.name
atexit.register(_RUNTIME_TEMP.cleanup)

from astrbot.api import AstrBotConfig
from astrbot.api.message_components import File, Plain
from astrbot.api.platform import AstrBotMessage, MessageMember, MessageType, PlatformMetadata
from astrbot.core.message import components as components_module
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("jmcomic_config_test", ROOT / "main.py")
plugin_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plugin_module)
Plugin = plugin_module.JMComicPDFPlugin
SCHEMA = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))


class FakeEvent:
    def __init__(self, group=False, admin=False):
        self.group = group
        self.admin = admin
        self.stopped = False

    def stop_event(self):
        self.stopped = True

    def get_group_id(self):
        return "456" if self.group else ""

    def is_admin(self):
        return self.admin

    def get_platform_name(self):
        return "other"

    def plain_result(self, text):
        return text

    def chain_result(self, chain):
        return chain


class PluginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / "data"
        fake_main = self.data / "plugins" / "astrbot_plugin_jmcomic" / "main.py"
        self.config = {key: spec["default"] for key, spec in SCHEMA.items()}
        with patch.object(plugin_module, "__file__", str(fake_main)):
            self.plugin = Plugin(None, self.config)
        self.pdf = self.plugin.base_mount_dir / "123.pdf"
        self.pdf.write_bytes(b"%PDF-test-fixture")

    async def run_command(self, event=None, comic_id="123"):
        return [item async for item in self.plugin.fetch_comic(event or FakeEvent(), comic_id)]

    async def test_group_and_permission_matrix(self):
        for group in (False, True):
            for allow_group in (False, True):
                for mode in ("仅管理员", "所有人"):
                    for admin in (False, True):
                        with self.subTest(group=group, allow_group=allow_group, mode=mode, admin=admin):
                            self.config.update(allow_group=allow_group, access_mode=mode, delivery_mode="直接发送 PDF")
                            event = FakeEvent(group, admin)
                            with patch.object(self.plugin, "_download_pdf") as download, patch.object(self.plugin, "_upload_pdf") as upload:
                                result = await self.run_command(event)
                            allowed = (not group or allow_group) and (mode == "所有人" or admin)
                            self.assertEqual(isinstance(result[-1], list), allowed)
                            self.assertTrue(event.stopped)
                            download.assert_not_called()
                            upload.assert_not_called()
                            if allowed:
                                self.assertEqual(result[-1][0].file, str(self.pdf))

    async def test_default_does_not_allow_group_admin(self):
        self.config["allow_group"] = True
        event = FakeEvent(group=True)
        event.platform_role = "owner"
        result = await self.run_command(event)
        self.assertIn("仅允许 AstrBot 管理员", result[-1])

    async def test_config_changes_apply_to_next_request(self):
        self.config["delivery_mode"] = "直接发送 PDF"
        self.assertIsInstance((await self.run_command())[-1], str)
        self.config["access_mode"] = "所有人"
        self.assertIsInstance((await self.run_command())[-1], list)

    async def test_invalid_id_never_downloads(self):
        self.config["access_mode"] = "所有人"
        for comic_id in ("", "abc", "../123", "１２３"):
            with self.subTest(comic_id=comic_id), patch.object(self.plugin, "_download_pdf") as download:
                result = await self.run_command(comic_id=comic_id)
                self.assertIn("用法", result[-1])
                download.assert_not_called()

    async def test_invalid_config_fails_closed(self):
        self.config["access_mode"] = "unexpected"
        self.assertIn("配置无效", (await self.run_command())[-1])
        self.config.update(access_mode="所有人", delivery_mode="unexpected")
        self.assertIn("配置无效", (await self.run_command())[-1])

    async def test_link_mode_uploads_cached_pdf(self):
        self.config["access_mode"] = "所有人"
        with patch.object(self.plugin, "_upload_pdf", return_value="https://tmpfiles.org/dl/123/test.pdf") as upload:
            result = await self.run_command()
        upload.assert_called_once_with(self.pdf)
        self.assertEqual(result[-1], "下载地址：\nhttps://tmpfiles.org/dl/123/test.pdf")

    async def test_uncached_request_downloads_then_sends(self):
        self.config.update(access_mode="所有人", delivery_mode="直接发送 PDF")
        with patch.object(self.plugin, "_find_cached_pdf", return_value=None), patch.object(self.plugin, "_download_pdf", return_value=self.pdf) as download:
            result = await self.run_command()
        download.assert_called_once_with("123")
        self.assertEqual(result[-1][0].file, str(self.pdf))

    async def test_busy_request_and_generator_close_release_lock(self):
        self.config.update(access_mode="所有人", delivery_mode="直接发送 PDF")
        first = self.plugin.fetch_comic(FakeEvent(), "123")
        await anext(first)
        self.assertIn("稍后重试", (await self.run_command())[-1])
        await first.aclose()
        self.assertFalse(self.plugin._download_lock.locked())

    async def test_upload_failure_does_not_leak_details_or_hold_lock(self):
        self.config["access_mode"] = "所有人"
        with patch.object(self.plugin, "_upload_pdf", side_effect=RuntimeError("secret diagnostic")), patch.object(plugin_module, "logger"):
            result = await self.run_command()
        self.assertIn("处理失败", result[-1])
        self.assertNotIn("secret", result[-1])
        self.assertFalse(self.plugin._download_lock.locked())

    async def test_titled_legacy_cache_is_reused(self):
        self.pdf.unlink()
        titled = self.pdf.with_name("[123] 旧标题.pdf")
        titled.write_bytes(b"cached-pdf")
        self.assertEqual(self.plugin._find_cached_pdf("123"), titled)
        self.assertIsNone(self.plugin._find_cached_pdf("12"))

    async def test_real_pdf_generation_and_temporary_cleanup(self):
        option = SimpleNamespace(dir_rule=SimpleNamespace(base_dir=None))
        option.build_jm_client = lambda: SimpleNamespace(get_album_detail=lambda _: SimpleNamespace(title='测试:/标题'))

        def fake_download(comic_id, actual_option):
            work = Path(actual_option.dir_rule.base_dir)
            for chapter, page in ((1, 2), (1, 10), (2, 1)):
                folder = work / str(chapter)
                folder.mkdir(exist_ok=True)
                with Image.new("RGB", (20, 30), color="blue") as image:
                    image.save(folder / f"{page}.png")

        with patch.object(plugin_module.jmcomic.JmOption, "default", return_value=option), patch.object(plugin_module.jmcomic, "download_album", side_effect=fake_download):
            pdf = self.plugin._download_pdf("456")
        self.assertEqual(pdf.name, "[456] 测试__标题.pdf")
        with pikepdf.open(pdf) as document:
            self.assertEqual(len(document.pages), 3)
        self.assertEqual(list(self.plugin.base_mount_dir.glob("tmp_*")), [])
        self.assertEqual(sorted([Path("10/1.png"), Path("2/10.png"), Path("2/2.png")], key=Plugin._image_sort_key), [Path("2/2.png"), Path("2/10.png"), Path("10/1.png")])

    async def test_download_failure_leaves_no_partial_pdf(self):
        option = SimpleNamespace(dir_rule=SimpleNamespace(base_dir=None), build_jm_client=Mock(side_effect=RuntimeError("no title")))
        with patch.object(plugin_module.jmcomic.JmOption, "default", return_value=option), patch.object(plugin_module.jmcomic, "download_album", side_effect=RuntimeError("download failed")):
            with self.assertRaises(RuntimeError):
                self.plugin._download_pdf("456")
        self.assertIsNone(self.plugin._find_cached_pdf("456"))
        self.assertEqual(list(self.plugin.base_mount_dir.glob("tmp_*")), [])

    async def test_upload_response_validation_and_timeout(self):
        for raw_url in ("http://tmpfiles.org/123/test.pdf", "https://tmpfiles.org/dl/123/test.pdf"):
            response = Mock()
            response.json.return_value = {"status": "success", "data": {"url": raw_url}}
            with patch.object(plugin_module.requests, "post", return_value=response) as post:
                url = self.plugin._upload_pdf(self.pdf)
            self.assertEqual(url, "https://tmpfiles.org/dl/123/test.pdf")
            self.assertEqual(post.call_args.kwargs["timeout"], (10, 120))
            self.assertTrue(post.call_args.kwargs["files"]["file"][1].closed)
            response.raise_for_status.assert_called_once()

        for payload in ({"status": "error"}, {"status": "success", "data": None}, {"status": "success", "data": {"url": "https://example.com/file"}}):
            response = Mock()
            response.json.return_value = payload
            with patch.object(plugin_module.requests, "post", return_value=response), self.assertRaises(RuntimeError):
                self.plugin._upload_pdf(self.pdf)
        with patch.object(plugin_module.requests, "post", side_effect=requests.Timeout), self.assertRaises(requests.Timeout):
            self.plugin._upload_pdf(self.pdf)

    async def test_real_astrbot_config_load_save_roundtrip(self):
        config_path = self.data / "config" / "test_config.json"
        config_path.parent.mkdir()
        config = AstrBotConfig(config_path=str(config_path), schema=SCHEMA)
        self.assertEqual(dict(config), {key: value["default"] for key, value in SCHEMA.items()})
        config.update(allow_group=True, access_mode="所有人", delivery_mode="直接发送 PDF")
        config.save_config()
        reloaded = AstrBotConfig(config_path=str(config_path), schema=SCHEMA)
        self.assertEqual(dict(config), dict(reloaded))

    @staticmethod
    def onebot_event(backend="llonebot", group=False):
        message = AstrBotMessage()
        message.type = MessageType.GROUP_MESSAGE if group else MessageType.FRIEND_MESSAGE
        message.self_id = "987654321"
        message.sender = MessageMember(user_id="123456789", nickname="测试用户")
        message.group_id = "456789123" if group else ""
        message.message = [Plain(text="/jm 123")]
        message.message_str = "/jm 123"
        message.raw_message = {"self_id": 987654321}
        message.message_id = "1"
        bot = SimpleNamespace(call_action=AsyncMock(return_value={"file_id": "test-file"}))
        event = AiocqhttpMessageEvent(
            "/jm 123", message, PlatformMetadata("aiocqhttp", backend, backend),
            message.group_id or message.sender.user_id, bot,
        )
        event.role = "admin"
        return event

    async def test_onebot_group_and_private_upload_contract(self):
        self.config.update(allow_group=True, delivery_mode="直接发送 PDF")
        self.pdf = self.pdf.rename(self.pdf.with_name("[123] 中文 文件 #1.pdf"))
        for backend in ("llonebot", "napcat"):
            for group in (False, True):
                with self.subTest(backend=backend, group=group):
                    event = self.onebot_event(backend, group)
                    with patch.dict(components_module.astrbot_config, {"callback_api_base": ""}):
                        result = await self.run_command(event)
                    params = {
                        "group_id" if group else "user_id": "456789123" if group else "123456789",
                        "file": str(self.pdf.resolve()),
                        "name": self.pdf.name,
                        "self_id": "987654321",
                    }
                    event.bot.call_action.assert_awaited_once_with(
                        "upload_group_file" if group else "upload_private_file", **params
                    )
                    self.assertEqual(result[-1].chain[0].text, "PDF 已发送。")
                    self.assertFalse(any(isinstance(part, File) for item in result for part in item.chain))

    async def test_onebot_uses_astrbot_callback_when_configured(self):
        self.config.update(allow_group=True, delivery_mode="直接发送 PDF")
        for group in (False, True):
            with self.subTest(group=group):
                event = self.onebot_event(group=group)
                with patch.dict(components_module.astrbot_config, {"callback_api_base": "http://astrbot:6185/"}), patch.object(components_module.file_token_service, "register_file", new_callable=AsyncMock, return_value="test-token") as register:
                    await self.run_command(event)
                register.assert_awaited_once_with(str(self.pdf.resolve()))
                self.assertEqual(event.bot.call_action.call_args.kwargs["file"], "http://astrbot:6185/api/file/test-token")

    async def test_onebot_failure_never_uploads_to_third_party(self):
        self.config["delivery_mode"] = "直接发送 PDF"
        event = self.onebot_event()
        event.bot.call_action.side_effect = RuntimeError("OneBot upload failed")
        with patch.dict(components_module.astrbot_config, {"callback_api_base": ""}), patch.object(self.plugin, "_upload_pdf") as upload, patch.object(plugin_module, "logger"):
            result = await self.run_command(event)
        upload.assert_not_called()
        self.assertIn("处理失败", result[-1].chain[0].text)
        self.assertFalse(self.plugin._download_lock.locked())

    async def test_onebot_link_mode_only_returns_link(self):
        event = self.onebot_event()
        with patch.object(self.plugin, "_upload_pdf", return_value="https://tmpfiles.org/dl/123/file.pdf"):
            result = await self.run_command(event)
        event.bot.call_action.assert_not_awaited()
        self.assertIn("https://tmpfiles.org/dl/123/file.pdf", result[-1].chain[0].text)

    async def test_onebot_invalid_recipient_is_not_sent(self):
        self.config["delivery_mode"] = "直接发送 PDF"
        event = self.onebot_event()
        event.message_obj.sender.user_id = "not-a-qq-id"
        with patch.object(plugin_module, "logger"):
            result = await self.run_command(event)
        event.bot.call_action.assert_not_awaited()
        self.assertIn("处理失败", result[-1].chain[0].text)


if __name__ == "__main__":
    unittest.main()
