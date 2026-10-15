import json
import re
import time
from .config import catalog, knowledge
from .llm import LanguageModel, ModelUnavailable
from .store import dumps

QUESTIONS = [
    ('goal', 'Какая у вас цель изучения английского?', ['Для работы', 'Для себя', 'Для ребёнка']),
    ('problem', 'Что сейчас мешает достичь этой цели?', ['Нет знаний', 'Нет практики', 'Не хватает времени']),
    ('level', 'Какой у вас опыт?', ['С нуля', 'Есть основы', 'Продвинутый']),
    ('timing', 'Когда хотите начать?', ['На этой неделе', 'В течение месяца', 'Пока изучаю']),
    ('format', 'Какой формат удобнее?', ['Онлайн', 'Очно', 'Нужен совет']),
    ('budget', 'Какой бюджет в рублях рассматриваете?', ['До 5000', 'До 10000', 'Нужен совет']),
    ('decision', 'Кто принимает решение об оплате?', ['Я', 'Родитель', 'Решаем вместе']),
]
CHOICES = {key: values for key, _, values in QUESTIONS if key in ('goal', 'level', 'format', 'decision')}
OBJECTIONS = {
    'price': 'С каким бюджетом сравниваете? Могу подобрать вариант из каталога или передать вопрос менеджеру.',
    'think': 'Что важно уточнить перед решением? Можем разобрать вопрос или подключить менеджера.',
    'time': 'Сколько времени готовы выделять в неделю? Покажу нагрузку выбранного курса или передам вопрос менеджеру.',
    'failed': 'Что именно не подошло в прошлый раз? Сравним это с форматом курса, а детали уточнит менеджер.',
    'consult': 'С кем хотите обсудить выбор и какие вопросы у этого человека? Подготовим информацию из каталога.',
    'compare': 'Какие критерии сравнения для вас главные? Расскажу о нашем формате по данным каталога.',
}


def budget_limit(value):
    numbers = re.findall(r'\d+(?:[.,]\d+)?', str(value).replace(' ', ''))
    if not numbers:
        return None
    amount = float(numbers[-1].replace(',', '.'))
    if re.search(r'тыс|[kк](?:\b|$)', str(value).lower()):
        amount *= 1000
    return amount


class Service:
    def __init__(self, store, model=None, products=None, kb=None):
        self.store = store
        self.model = model or LanguageModel()
        self.products = catalog() if products is None else products
        self.kb = knowledge() if kb is None else kb

    def eligible(self, answers):
        limit = budget_limit(answers.get('budget'))
        return [p for p in self.products if p.get('approved') is True
                and (not answers.get('goal') or answers['goal'] in p['goals'])
                and (not answers.get('level') or answers['level'] in p['levels'])
                and (answers.get('format') in (None, 'Нужен совет', p['format']))
                and (limit is None or p['price'] <= limit)]

    @staticmethod
    def question(state):
        for key, question, buttons in QUESTIONS:
            if not state['answers'].get(key):
                return key, question, buttons
        return None, '', []

    def product_text(self, product):
        return (f"{product['name']} — {product['price']} {product['currency']} за {product['duration']}.\n"
                f"{product['description']}\n{product['format']}, {product['schedule']}.\n"
                'Передать заявку менеджеру для обсуждения следующего шага?')

    def handoff(self, cid, state, reason, text):
        state['status'] = 'handoff'
        self.store.event(cid, 'handoff', {'reason': reason})
        context = (f'LeadPilot / {cid}\nПричина: {reason}\n'
                   f'Ответы: {dumps(state["answers"])}\nПродукт: {state.get("product_id")}\n'
                   + '\n'.join(f'{m["direction"]}: {m["text"]}' for m in self.store.history(cid))
                   + '\nКлиент: ' + text)
        # Chunk before enqueue: retries do not repeat already delivered chunks.
        for start in range(0, len(context), 3500):
            self.store.db.execute('INSERT INTO notifications(client,text) VALUES(?,?)', (cid, context[start:start + 3500]))
        return 'Заявка сохранена для менеджера вместе с контекстом диалога. Онлайн-запись и оплата в MVP не выполняются.', []

    def handle(self, cid, text, request_id, channel='web', external_id=''):
        started = time.monotonic()
        with self.store.lock, self.store.db:
            row = self.store.db.execute('SELECT response FROM requests WHERE client=? AND id=?', (cid, request_id)).fetchone()
            if row:
                return json.loads(row[0])
            client = self.store.client(cid)
            state = client['state'] if client else {'answers': {}, 'status': 'new', 'failures': 0}
            low = text.strip().casefold()
            if low == '/delete':
                self.store.db.execute('DELETE FROM clients WHERE id=?', (cid,))
                return {'text': 'Локальные данные удалены. Копии в Telegram и у ИИ-провайдера не удалены. Начать: /start.', 'buttons': ['/start'], 'status': 'new', 'answers': {}}
            if low == '/start' or state['status'] == 'new':
                if low not in ('согласен', 'согласна', 'согласен, начать'):
                    if client and low == '/start':
                        state = {'answers': {}, 'status': 'new', 'failures': 0}
                        self.store.save(cid, channel, external_id, state)
                    # No persistent profile or AI request before consent.
                    return {'text': ('Я ИИ-ассистент LeadPilot. Демонстрационный каталог английского.\n' if self.kb['demo_data'] else 'Я ИИ-ассистент LeadPilot.\n') + self.kb['privacy_notice'],
                            'buttons': ['Согласен, начать'], 'status': 'new', 'answers': {}}
                state = {'answers': {}, 'status': 'qualifying', 'failures': 0}
                self.store.save(cid, channel, external_id, state)
                self.store.event(cid, 'consent')
                _, reply, buttons = self.question(state)
            elif low in ('/stop', 'стоп', 'отписаться'):
                state['status'] = 'stopped'
                self.store.db.execute('DELETE FROM notifications WHERE client=? AND delivered IS NULL', (cid,))
                reply, buttons = 'Общение остановлено. Вернуться: /start. Удалить данные: /delete.', ['/start', '/delete']
            elif state['status'] == 'stopped':
                reply, buttons = 'Общение остановлено. Для нового диалога: /start.', ['/start']
            elif state['status'] == 'handoff':
                self.store.db.execute('INSERT INTO notifications(client,text) VALUES(?,?)', (cid, f'Дополнение / {cid}\n{text}'))
                reply, buttons = 'Дополнение сохранено для менеджера. Можно остановить диалог или удалить данные.', ['/stop', '/delete']
            elif low in ('менеджер', '/manager', 'человек') or any(w in low for w in ('возврат', 'жалоба', 'конфликт')):
                reply, buttons = self.handoff(cid, state, 'direct_request', text)
            elif low in ('ты ии?', 'ты бот?', 'вы бот?', 'вы ии?'):
                reply, buttons = 'Да, я ИИ-ассистент. Могу подобрать курс по базе знаний или передать вопрос человеку.', ['Менеджер']
            elif low == 'продолжить подбор':
                key, question, options = self.question(state)
                product = next((p for p in self.eligible(state['answers']) if p['id'] == state.get('product_id')), None)
                reply, buttons = (question, options) if key else ((self.product_text(product), ['Менеджер']) if product else ('Уточните цель и бюджет, чтобы пересмотреть подбор.', ['Менеджер']))
            else:
                key, question, _ = self.question(state)
                inference_state = {**state, 'current_question': {'key': key, 'text': question}}
                try:
                    understood = self.model.understand(text, inference_state, self.eligible(state['answers']), self.kb['faq'], self.store.history(cid))
                except ModelUnavailable:
                    return {'text': 'ИИ сейчас недоступен. Проверьте ключ и модель на сервере или передайте заявку менеджеру. Сообщение не обработано; можно повторить.',
                            'buttons': ['Менеджер'], 'status': state['status'], 'answers': state['answers'], 'error': 'llm_unavailable'}
                if understood.intent in ('manager', 'complaint'):
                    reply, buttons = self.handoff(cid, state, understood.intent, text)
                else:
                    for name, value in understood.answers.model_dump().items():
                        if value and (name not in CHOICES or value in CHOICES[name]):
                            state['answers'][name] = value[:500]
                    eligible = self.eligible(state['answers'])
                    selected = next((p for p in eligible if p['id'] == understood.product_id), None)
                    old_product = next((p for p in eligible if p['id'] == state.get('product_id')), None)
                    key, question, options = self.question(state)
                    if state.get('product_id') and not old_product:
                        state.pop('product_id', None)
                        state['status'] = 'qualifying'
                    if budget_limit(state['answers'].get('budget')) and budget_limit(state['answers']['budget']) > self.kb['escalation_amount']:
                        reply, buttons = self.handoff(cid, state, 'amount_threshold', text)
                    elif understood.intent == 'identity':
                        reply, buttons = 'Да, я ИИ-ассистент. Факты о курсах беру из утверждённого каталога.', ['Менеджер']
                    elif understood.intent == 'objection' and understood.objection:
                        self.store.event(cid, 'objection', {'reason': understood.objection})
                        reply, buttons = OBJECTIONS[understood.objection], ['Менеджер', 'Продолжить подбор']
                    elif understood.intent == 'faq':
                        faq = next((f for f in self.kb['faq'] if f['id'] == understood.faq_id), None)
                        if faq:
                            state['failures'] = 0
                            reply, buttons = faq['answer'], ['Продолжить подбор', 'Менеджер']
                        else:
                            reply, buttons = self.handoff(cid, state, 'missing_faq', text)
                    elif understood.intent == 'product' and (selected or old_product):
                        product = selected or old_product
                        reply = self.product_text(product) + '\nПреподаватель: ' + product['teachers']
                        buttons = ['Менеджер', 'Продолжить подбор']
                    elif understood.intent == 'unknown' or not understood.valid:
                        state['failures'] += 1
                        if state['failures'] >= 3:
                            reply, buttons = self.handoff(cid, state, 'three_failures', text)
                        else:
                            reply, buttons = 'В базе нет подтверждённого ответа или я не понял запрос. Уточните, пожалуйста, либо подключите менеджера.', ['Менеджер']
                    elif key:
                        state['failures'] = 0
                        reply, buttons = question, options
                    else:
                        state['failures'] = 0
                        if state['status'] != 'offered':
                            self.store.event(cid, 'qualified')
                        if not selected and eligible:
                            # Model can extract all answers in one turn: the initial eligible
                            # set may exclude a product until explicit corrections are applied.
                            selected = sorted(eligible, key=lambda p: p['price'])[0]
                        if selected:
                            state['status'] = 'offered'
                            state['product_id'] = selected['id']
                            reply, buttons = self.product_text(selected), ['Менеджер', 'Дорого', 'Подумаю']
                        else:
                            reply, buttons = self.handoff(cid, state, 'no_matching_product', text)
            self.store.save(cid, channel, external_id, state)
            now = time.time()
            self.store.db.executemany('INSERT INTO messages(client,direction,text,created) VALUES(?,?,?,?)',
                                      [(cid, 'Клиент', text, now), (cid, 'Бот', reply, now)])
            self.store.event(cid, 'response', {'seconds': time.monotonic() - started})
            result = {'text': reply, 'buttons': buttons, 'status': state['status'], 'answers': state['answers'], 'product_id': state.get('product_id')}
            self.store.db.execute('INSERT INTO requests VALUES(?,?,?)', (cid, request_id, dumps(result)))
            return result

    def cleanup(self):
        with self.store.lock, self.store.db:
            self.store.db.execute('DELETE FROM clients WHERE updated<?', (time.time() - self.kb['retention_days'] * 86400,))
