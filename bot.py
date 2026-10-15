"""LeadPilot: минимальный сценарный Telegram-продавец. Python 3.12+."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.request

ROOT = Path(__file__).resolve().parent
QUESTIONS = [
    ('goal', 'Какая у вас цель?', ['Для работы', 'Для себя', 'Для ребёнка']),
    ('problem', 'Что сейчас мешает достичь этой цели?', ['Нет знаний', 'Нет практики', 'Не хватает времени']),
    ('level', 'Какой у вас опыт?', ['С нуля', 'Есть основы', 'Продвинутый']),
    ('timing', 'Когда хотите начать?', ['На этой неделе', 'В течение месяца', 'Пока изучаю']),
    ('format', 'Какой формат удобнее?', ['Онлайн', 'Очно', 'Нужен совет']),
    ('budget', 'Какой бюджет рассматриваете? Можно написать сумму.', ['Нужен подбор', 'Обсужу с менеджером']),
    ('decision', 'Кто принимает решение об оплате?', ['Я', 'Родитель', 'Решаем вместе']),
]


class SalesBot:
    def __init__(self, db='leads.db', catalog=None):
        self.db = sqlite3.connect(db)
        self.db.execute('CREATE TABLE IF NOT EXISTS leads (id INTEGER PRIMARY KEY, state TEXT)')
        self.catalog = catalog if catalog is not None else json.loads((ROOT / 'catalog.json').read_text(encoding='utf-8'))['products']

    def save(self, uid, state):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO leads VALUES (?, ?)', (uid, json.dumps(state, ensure_ascii=False)))

    def handle(self, uid, message):
        row = self.db.execute('SELECT state FROM leads WHERE id=?', (uid,)).fetchone()
        s = json.loads(row[0]) if row else {'step': -1, 'answers': {}, 'history': [], 'status': 'new'}
        text = message.strip()[:3000]
        low = text.lower()
        if low == '/delete':
            with self.db:
                self.db.execute('DELETE FROM leads WHERE id=?', (uid,))
            return 'Данные из локальной базы удалены. Для начала: /start', [], None
        handoff = None
        if low in ('/stop', 'стоп', 'отписаться'):
            s['status'] = 'stopped'
            reply, buttons = 'Общение остановлено. Вернуться: /start. Удалить данные: /delete.', []
        elif s['status'] == 'stopped' and low != '/start':
            return 'Общение остановлено. Вернуться: /start.', [], None
        elif low == '/start' or (s['step'] == -1 and low != 'согласен'):
            s['step'] = -1
            s['status'] = 'new'
            reply = ('Здравствуйте! Я автоматический ассистент LeadPilot. Помогу подобрать обучение.\n'
                     'С вашего согласия сохраним Telegram ID, ответы и переписку в локальной базе оператора '
                     'и передадим заявку менеджеру. /delete — удалить локальные данные; /stop — остановить. Продолжить?')
            buttons = ['Согласен', 'Стоп']
            if row:
                self.save(uid, s)
            return reply, buttons, None
        elif s['step'] == -1:
            s['status'] = 'qualifying'
            s['step'] = 0
            reply, buttons = QUESTIONS[0][1:]
        elif any(word in low for word in ('менеджер', 'человек', 'возврат', 'жалоб', 'конфликт')):
            s['status'] = 'handoff'
            handoff = f'Заявка {uid}\n' + json.dumps(s['answers'], ensure_ascii=False) + '\nПереписка:\n' + '\n'.join(s['history'] + ['Клиент: ' + text])
            reply, buttons = 'Заявка сохранена для менеджера. Он сможет продолжить с учётом ваших ответов.', []
        elif s['status'] == 'handoff':
            reply, buttons = 'Заявка ожидает менеджера. Дополнение сохранено. Начать заново: /start.', []
        elif s['step'] < len(QUESTIONS):
            s['answers'][QUESTIONS[s['step']][0]] = text
            s['step'] += 1
            if s['step'] < len(QUESTIONS):
                reply, buttons = QUESTIONS[s['step']][1:]
            else:
                matches = [p for p in self.catalog if p.get('goal') == s['answers']['goal']]
                if matches:
                    p = matches[0]
                    reply = f"Под вашу цель есть вариант: {p['name']}.\n{p['description']}\nЦена: {p['price']}.\nОбсудим детали с менеджером?"
                else:
                    reply = 'Спасибо! Ответы сохранены. В базе пока нет подтверждённого предложения под вашу цель. Передать заявку менеджеру?'
                buttons = ['Менеджер', 'Стоп']
        else:
            reply, buttons = 'Этот вопрос лучше уточнить у менеджера. Передать заявку?', ['Менеджер', 'Стоп']
        s['history'].extend(['Клиент: ' + text, 'Бот: ' + reply])
        self.save(uid, s)
        return reply, buttons, handoff


def api(token, method, **data):
    request = urllib.request.Request(f'https://api.telegram.org/bot{token}/{method}',
                                     data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=40) as response:
        result = json.load(response)
    if not result.get('ok'):
        raise RuntimeError('Telegram API rejected request')
    return result['result']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true', help='Локальный диалог без Telegram')
    args = parser.parse_args()
    bot = SalesBot(str(ROOT / 'leads.db'))
    if args.demo:
        print('Введите /start. Выход: /quit')
        while True:
            msg = input('Вы: ')
            if msg == '/quit':
                break
            reply, buttons, _ = bot.handle(1, msg)
            print(reply, '\nВарианты:', ', '.join(buttons))
        return
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    manager = os.environ.get('MANAGER_CHAT_ID')
    if not token or not manager:
        parser.error('Задайте TELEGRAM_BOT_TOKEN и MANAGER_CHAT_ID или используйте --demo')
    offset = 0
    print('LeadPilot запущен. Ctrl+C — остановить.')
    while True:
        try:
            for update in api(token, 'getUpdates', offset=offset, timeout=25, allowed_updates=['message']):
                msg = update.get('message', {})
                chat = msg.get('chat', {})
                if chat.get('type') == 'private':
                    uid = chat['id']
                    if not msg.get('text'):
                        api(token, 'sendMessage', chat_id=uid, text='Пока понимаю только текст. Напишите сообщение или «Менеджер».')
                    else:
                        reply, buttons, handoff = bot.handle(uid, msg['text'])
                        if handoff:
                            for start in range(0, len(handoff), 4000):
                                api(token, 'sendMessage', chat_id=manager, text=handoff[start:start + 4000])
                        api(token, 'sendMessage', chat_id=uid, text=reply,
                            reply_markup={'keyboard': [[{'text': b}] for b in buttons], 'resize_keyboard': True} if buttons else {'remove_keyboard': True})
                offset = update['update_id'] + 1
        except (OSError, RuntimeError):
            print('Ошибка связи с Telegram. Повтор через 5 секунд; токен не выводится.')
            time.sleep(5)


if __name__ == '__main__':
    main()
