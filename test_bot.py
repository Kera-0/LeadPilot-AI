import unittest
from bot import SalesBot, QUESTIONS


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.bot = SalesBot(':memory:', [])

    def tearDown(self):
        self.bot.db.close()

    def test_consent(self):
        self.bot.handle(10, '/start')
        self.assertIsNone(self.bot.db.execute('SELECT * FROM leads').fetchone())

    def test_qualification_and_handoff(self):
        self.bot.handle(10, 'Согласен')
        for _, _, buttons in QUESTIONS:
            reply, _, _ = self.bot.handle(10, buttons[0])
        self.assertIn('нет подтверждённого предложения', reply)
        _, _, context = self.bot.handle(10, 'Менеджер')
        self.assertIn('Для работы', context)
        self.assertIn('Переписка', context)

    def test_stop_delete_restart(self):
        self.bot.handle(10, 'Согласен')
        self.bot.handle(10, '/stop')
        self.assertIn('остановлено', self.bot.handle(10, 'привет')[0])
        self.bot.handle(10, '/start')
        self.assertEqual(self.bot.handle(10, 'Согласен')[0], QUESTIONS[0][1])
        self.bot.handle(10, '/delete')
        self.assertIsNone(self.bot.db.execute('SELECT * FROM leads').fetchone())

    def test_complaint_handoff(self):
        self.bot.handle(10, 'Согласен')
        self.assertIsNotNone(self.bot.handle(10, 'Хочу возврат')[2])


if __name__ == '__main__':
    unittest.main()
