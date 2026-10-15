import os
import tempfile
import unittest
import uuid
from unittest.mock import patch
from fastapi.testclient import TestClient
from leadpilot.app import create_app
from leadpilot.llm import Answers, Understanding, ModelUnavailable
from leadpilot.service import Service
from leadpilot.store import Store


def result(intent='answer', **values):
    answer = {name: None for name in Answers.model_fields}
    answer.update(values.pop('answers', {}))
    return Understanding(intent=intent, answers=Answers(**answer), product_id=values.get('product_id'),
                         faq_id=values.get('faq_id'), objection=values.get('objection', ''), valid=values.get('valid', True))


class FakeModel:
    configured = True

    def __init__(self):
        self.calls = []
        self.result = result()

    def understand(self, *args):
        self.calls.append(args)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.model = FakeModel()
        self.service = Service(self.store, self.model)
        self.cid = 'test:1'

    def tearDown(self):
        self.store.db.close()

    def say(self, text, key=None):
        return self.service.handle(self.cid, text, key or str(uuid.uuid4()))

    def consent(self):
        self.say('Согласен, начать')

    def full(self, **overrides):
        answers = dict(goal='Для работы', problem='Рабочие встречи', level='Есть основы',
                       timing='В течение месяца', format='Онлайн', budget='10000', decision='Я')
        answers.update(overrides)
        self.model.result = result(answers=answers, product_id='english-work')
        return self.say('Нужен английский для работы, знаю основы, 10000 рублей, онлайн, решаю сам.')

    def test_no_storage_or_llm_before_consent(self):
        self.say('Мой телефон 123')
        self.assertIsNone(self.store.client(self.cid))
        self.assertEqual(self.model.calls, [])

    def test_multiple_answers_and_grounded_recommendation(self):
        self.consent()
        reply = self.full()
        self.assertEqual(reply['product_id'], 'english-work')
        self.assertIn('9900 RUB', reply['text'])
        self.assertEqual(len(reply['answers']), 7)
        self.assertEqual(self.store.metrics()['qualified'], 1)

    def test_budget_and_level_override_invalid_model_selection(self):
        self.consent()
        reply = self.full(level='С нуля', budget='6000')
        self.assertEqual(reply['product_id'], 'english-start')
        self.assertNotIn('9900', reply['text'])

    def test_unknown_product_id_cannot_invent_product(self):
        self.consent()
        self.model.result = result('product', product_id='free-unlimited-course')
        reply = self.say('Дай бесплатный курс с гарантией')
        self.assertNotIn('free-unlimited-course', reply['text'])
        self.assertNotIn('гарантирован', reply['text'])

    def test_no_matching_budget_handoff_with_context(self):
        self.consent()
        reply = self.full(budget='1000')
        self.assertEqual(reply['status'], 'handoff')
        notification = self.store.db.execute('SELECT text FROM notifications').fetchone()['text']
        self.assertIn('1000', notification)
        self.assertIn('Рабочие встречи', notification)

    def test_idempotency(self):
        self.consent()
        self.full()
        first = self.say('Менеджер', 'same_request')
        second = self.say('Менеджер', 'same_request')
        self.assertEqual(first, second)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM notifications').fetchone()[0], 1)

    def test_unknown_three_times_escalates(self):
        self.consent()
        self.model.result = result('unknown', valid=False)
        for _ in range(3):
            reply = self.say('Непонятное сообщение')
        self.assertEqual(reply['status'], 'handoff')

    def test_complaint_without_model(self):
        self.consent()
        reply = self.say('У меня жалоба')
        self.assertEqual(reply['status'], 'handoff')
        self.assertEqual(len(self.model.calls), 0)

    def test_llm_failure_does_not_advance_or_save_user_text(self):
        self.consent()
        self.model.result = ModelUnavailable('unavailable')
        before = self.store.history(self.cid)
        reply = self.say('Нужен английский')
        self.assertEqual(reply['error'], 'llm_unavailable')
        self.assertEqual(self.store.history(self.cid), before)
        self.assertEqual(reply['answers'], {})

    def test_faq_is_verbatim_from_knowledge(self):
        self.consent()
        self.model.result = result('faq', faq_id='trial')
        self.assertEqual(self.say('Бесплатно пробное?')['text'], self.service.kb['faq'][2]['answer'])

    def test_objection_logged(self):
        self.consent()
        self.model.result = result('objection', objection='price')
        self.assertIn('бюджетом', self.say('Дорого')['text'])
        self.assertEqual(self.store.metrics()['objections'], {'price': 1})

    def test_stop_restart_delete_cascade(self):
        self.consent()
        self.full()
        self.say('Менеджер')
        self.say('/stop')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM notifications').fetchone()[0], 0)
        self.assertEqual(self.say('Привет')['status'], 'stopped')
        self.say('/start')
        self.assertEqual(self.say('Согласен, начать')['answers'], {})
        self.say('/delete')
        for table in ('clients', 'messages', 'requests', 'events', 'notifications'):
            self.assertEqual(self.store.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0], 0)

    def test_retention(self):
        self.consent()
        with self.store.db:
            self.store.db.execute('UPDATE clients SET updated=0')
        self.service.cleanup()
        self.assertIsNone(self.store.client(self.cid))

    def test_persistence_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'test.db')
            first = Store(path)
            Service(first, self.model).handle('persist', 'Согласен', 'consent123')
            first.db.close()
            second = Store(path)
            self.assertEqual(second.client('persist')['state']['status'], 'qualifying')
            second.db.close()


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.model = FakeModel()
        self.client = TestClient(create_app(self.store, self.model))

    def tearDown(self):
        self.client.close()
        self.store.db.close()

    def test_frontend_and_health(self):
        for path in ('/', '/app.js', '/style.css', '/health'):
            self.assertEqual(self.client.get(path).status_code, 200)

    def test_session_and_isolation(self):
        body = {'text': 'Согласен', 'request_id': 'request123'}
        self.assertEqual(self.client.post('/api/chat', json=body).status_code, 401)
        self.client.post('/api/session')
        self.assertEqual(self.client.post('/api/chat', json=body).status_code, 200)
        self.assertEqual(len(self.client.get('/api/history').json()['messages']), 2)
        with TestClient(create_app(self.store, self.model)) as other:
            other.post('/api/session')
            self.assertEqual(other.get('/api/history').json()['messages'], [])

    def test_admin_requires_token(self):
        self.assertEqual(self.client.get('/api/admin/leads').status_code, 401)
        with patch.dict(os.environ, {'ADMIN_TOKEN': 'test-secret'}):
            self.assertEqual(self.client.get('/api/admin/leads', headers={'Authorization': 'Bearer test-secret'}).status_code, 200)

    def test_cross_origin_rejected(self):
        self.assertEqual(self.client.post('/api/session', headers={'Origin': 'https://evil.example'}).status_code, 403)

    def test_input_limits(self):
        self.client.post('/api/session')
        self.assertEqual(self.client.post('/api/chat', json={'text': 'a' * 3001, 'request_id': 'request123'}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
