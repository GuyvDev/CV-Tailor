from __future__ import annotations

import importlib.util
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import CommandHandler


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cv_docker_telegram_bot", ROOT / "telegram-bot/app/main.py")
bot = importlib.util.module_from_spec(spec)
with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123456:unit-test-token", "TELEGRAM_ALLOWED_USER_IDS": "42"}):
    spec.loader.exec_module(bot)


class BotCommandTests(unittest.IsolatedAsyncioTestCase):
    def update(self, user_id=42):
        message = SimpleNamespace(reply_text=AsyncMock())
        return SimpleNamespace(effective_user=SimpleNamespace(id=user_id), effective_message=message, message=message)

    async def test_help_is_registered_and_documents_every_command(self) -> None:
        application = bot.build_application()
        handlers = [handler for group in application.handlers.values() for handler in group if isinstance(handler, CommandHandler)]
        commands = {command for handler in handlers for command in handler.commands}
        self.assertIn("help", commands)
        self.assertIn("name_cv", commands)
        help_handler = next(handler for handler in handlers if "help" in handler.commands)
        update = self.update()
        await help_handler.callback(update, SimpleNamespace(args=[]))
        update.effective_message.reply_text.assert_awaited_once()
        args, kwargs = update.effective_message.reply_text.await_args
        text = args[0]
        self.assertEqual(set(re.findall(r"^/([a-z_]+) — ", text, re.M)), commands)
        self.assertLessEqual(len(text), 4096)
        self.assertNotIn("parse_mode", kwargs)
        for _name, _handler, description in bot.BOT_COMMANDS:
            self.assertIn(description, text)
        for usage in ("/name_cv on", "/name_cv off", "/score <id>", "/edit <id>", "#cv", ".txt", "systems:"):
            self.assertIn(usage, text)

    async def test_start_shows_the_same_complete_help(self) -> None:
        update = self.update()
        await bot.cmd_start(update, SimpleNamespace(args=[]))
        update.effective_message.reply_text.assert_awaited_once_with(bot.command_help_text())

    async def test_help_respects_the_allowlist(self) -> None:
        update = self.update(user_id=7)
        await bot.cmd_help(update, SimpleNamespace(args=[]))
        update.effective_message.reply_text.assert_not_awaited()

    async def test_name_cv_on_off_and_toggle(self) -> None:
        for args, current, expected in ((["on"], False, True), (["off"], True, False), ([], False, True), ([], True, False)):
            with self.subTest(args=args, current=current):
                update = self.update()
                with (
                    patch.object(bot, "request_app_settings", AsyncMock(return_value={"append_company_to_output_name": current, "output_basename": "CV-Candidate"})),
                    patch.object(bot, "set_company_filename_enabled", AsyncMock()) as setter,
                ):
                    await bot.cmd_name_cv(update, SimpleNamespace(args=args))
                setter.assert_awaited_once_with(expected)
                text = update.message.reply_text.await_args.args[0]
                self.assertIn("CV-Candidate", text)
                if expected:
                    self.assertIn("-Apple.pdf", text)
                    self.assertIn("-Amazon.pdf", text)

    async def test_sent_filename_matches_api_selected_name_for_pdf_and_typst(self) -> None:
        for employer in ("Apple", "Amazon"):
            metadata = {"pdf_url": f"/files/example/CV-Candidate-{employer}.pdf", "typst_url": f"/files/example/CV-Candidate-{employer}.typ"}
            self.assertEqual(bot.artifact_filename(metadata, "pdf"), f"CV-Candidate-{employer}.pdf")
            self.assertEqual(bot.artifact_filename(metadata, "typst"), f"CV-Candidate-{employer}.typ")


if __name__ == "__main__":
    unittest.main()
