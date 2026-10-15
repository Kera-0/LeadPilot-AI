"""CLI / Telegram entry point. Web: python -m uvicorn leadpilot.app:app."""
import argparse
import json
import time
import uuid
import httpx
from leadpilot.config import ROOT, env
from leadpilot.llm import ModelUnavailable
from leadpilot.service import Service
from leadpilot.store import Store


def telegram(method, **payload):
    result = httpx.post(f'https://api.telegram.org/bot{env("TELEGRAM_BOT_TOKEN")}/{method}',
                        json=payload, timeout=35)
    result.raise_for_status()
    body = result.json()
    if not body.get('ok'):
        raise RuntimeError('Telegram request failed')
    return body['result']


def notifications(service):
    if not env('TELEGRAM_BOT_TOKEN') or not env('MANAGER_CHAT_ID'):
        return
    store = service.store
    with store.lock:
        pending = list(store.db.execute('SELECT * FROM notifications WHERE delivered IS NULL AND available<=? LIMIT 10', (time.time(),)))
    for row in pending:
        try:
            telegram('sendMessage', chat_id=env('MANAGER_CHAT_ID'), text=row['text'])
        except (httpx.HTTPError, RuntimeError):
            with store.lock, store.db:
                store.db.execute('UPDATE notifications SET attempts=attempts+1,available=? WHERE id=?',
                                 (time.time() + min(3600, 5 * 2 ** min(row['attempts'], 9)), row['id']))
        else:
            with store.lock, store.db:
                store.db.execute('UPDATE notifications SET delivered=? WHERE id=?', (time.time(), row['id']))


def voice_text(service, cid, voice):
    with service.store.lock:
        client = service.store.client(cid)
        if not client or client['state']['status'] in ('new', 'stopped'):
            return None
    if voice.get('file_size', 0) > 10 * 1024 * 1024:
        return None
    info = telegram('getFile', file_id=voice['file_id'])
    if info.get('file_size', 0) > 10 * 1024 * 1024:
        return None
    with httpx.stream('GET', f'https://api.telegram.org/file/bot{env("TELEGRAM_BOT_TOKEN")}/{info["file_path"]}', timeout=20) as response:
        response.raise_for_status()
        chunks, total = [], 0
        for chunk in response.iter_bytes():
            total += len(chunk)
            if total > 10 * 1024 * 1024:
                return None
            chunks.append(chunk)
    return service.model.transcribe(b''.join(chunks))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true', help='Живой LLM-диалог в терминале')
    parser.add_argument('--worker', action='store_true', help='Только очередь уведомлений менеджеру')
    args = parser.parse_args()
    service = Service(Store(env('DATABASE_PATH', str(ROOT / 'leads.db'))))
    service.cleanup()
    if args.demo:
        print('LeadPilot / живой LLM. /start — начать, /quit — выход.')
        while True:
            text = input('Вы: ')
            if text == '/quit':
                return
            result = service.handle('cli:demo', text, str(uuid.uuid4()), 'cli')
            print(result['text'], '\nВарианты:', ', '.join(result['buttons']))
    if not env('TELEGRAM_BOT_TOKEN') or not env('MANAGER_CHAT_ID'):
        parser.error('Задайте TELEGRAM_BOT_TOKEN и MANAGER_CHAT_ID или используйте --demo; веб: python -m uvicorn leadpilot.app:app')
    print('LeadPilot Telegram запущен. Ctrl+C — остановить.')
    last_cleanup = 0
    while True:
        try:
            notifications(service)
            if time.time() - last_cleanup > 3600:
                service.cleanup()
                last_cleanup = time.time()
            if args.worker:
                time.sleep(3)
                continue
            with service.store.lock:
                row = service.store.db.execute("SELECT value FROM settings WHERE key='telegram_offset'").fetchone()
            offset = int(row['value']) if row else 0
            for update in telegram('getUpdates', offset=offset, timeout=25, allowed_updates=['message']):
                message = update.get('message', {})
                chat = message.get('chat', {})
                if chat.get('type') == 'private' and str(chat['id']) != env('MANAGER_CHAT_ID'):
                    cid = f'telegram:{chat["id"]}'
                    text = message.get('text', '')
                    if message.get('voice'):
                        try:
                            text = voice_text(service, cid, message['voice']) or ''
                        except ModelUnavailable:
                            text = ''
                    if text:
                        result = service.handle(cid, text[:3000], f'tg_{update["update_id"]}', 'telegram', str(chat['id']))
                        buttons = result['buttons']
                        telegram('sendMessage', chat_id=chat['id'], text=result['text'], reply_markup={
                            'keyboard': [[{'text': b}] for b in buttons], 'resize_keyboard': True
                        } if buttons else {'remove_keyboard': True})
                    else:
                        telegram('sendMessage', chat_id=chat['id'], text='Напишите текстом. Голос доступен после согласия при настроенной модели; лимит 10 МБ. Начать: /start.')
                with service.store.lock, service.store.db:
                    service.store.db.execute("INSERT OR REPLACE INTO settings VALUES('telegram_offset',?)", (str(update['update_id'] + 1),))
        except (httpx.HTTPError, RuntimeError):
            print('Временная ошибка Telegram. Повтор через 5 секунд; секреты не выводятся.')
            time.sleep(5)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nОстановлено.')
