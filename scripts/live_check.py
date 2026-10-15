"""Real paid LLM smoke check. Run explicitly: python -m scripts.live_check."""
import uuid
from leadpilot.service import Service
from leadpilot.store import Store


def main():
    store = Store(':memory:')
    service = Service(store)
    service.handle('check', 'Согласен', str(uuid.uuid4()))
    reply = service.handle('check', 'Мне нужен английский для работы: участвую во встречах, основы уже знаю. Хочу начать через месяц, только онлайн. Бюджет до 10000 рублей, решение принимаю сам.', str(uuid.uuid4()))
    if reply.get('error'):
        raise SystemExit('LLM недоступна. Задайте ключ и LLM_MODEL в .env; проверьте доступ к API.')
    assert reply['answers'].get('goal') == 'Для работы', reply
    assert reply['answers'].get('level') == 'Есть основы', reply
    assert reply.get('product_id') in ('english-start', 'english-work'), reply
    print('LIVE LLM OK:', reply['text'])
    store.db.close()


if __name__ == '__main__':
    main()
