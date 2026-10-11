import asyncio
import unittest
from types import SimpleNamespace
from probnik_welcome import RULES_BY_CHAT_ID, welcome_text, welcome_new_members

class FakeBot:
    def __init__(self):
        self.sent = []
    async def send_message(self, **kwargs):
        self.sent.append(kwargs)

def update(chat_id, members, kind="supergroup"):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id, type=kind),
        effective_message=SimpleNamespace(new_chat_members=members),
    )

class WelcomeRegression(unittest.TestCase):
    def test_five_chat_links(self):
        self.assertEqual(len(RULES_BY_CHAT_ID), 5)
        for chat_id, link in RULES_BY_CHAT_ID.items():
            msg = welcome_text(chat_id)
            self.assertIn(link, msg)
            self.assertIn("<b>ПРОЦЕНИТЬ</b>", msg)
            self.assertIn("раздел правил", msg)

    def test_only_one_message_per_human(self):
        bot = FakeBot()
        members = [SimpleNamespace(id=1, is_bot=False),
                   SimpleNamespace(id=1, is_bot=False),
                   SimpleNamespace(id=2, is_bot=True)]
        asyncio.run(welcome_new_members(update(-1001660190650, members),
                                        SimpleNamespace(bot=bot)))
        self.assertEqual(len(bot.sent), 1)
        self.assertEqual(bot.sent[0]["parse_mode"], "HTML")

    def test_unknown_chat_and_private_are_ignored(self):
        bot = FakeBot()
        human = [SimpleNamespace(id=1, is_bot=False)]
        for event in (update(-1009999999999, human),
                      update(-1001660190650, human, "private"),
                      update(-1001660190650, [])):
            asyncio.run(welcome_new_members(event, SimpleNamespace(bot=bot)))
        self.assertEqual(bot.sent, [])

if __name__ == "__main__":
    unittest.main()
