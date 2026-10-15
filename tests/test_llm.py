import os
import unittest
from unittest.mock import patch, MagicMock
from leadpilot.llm import LanguageModel, ModelUnavailable
from test_service import result


class LLMTests(unittest.TestCase):
    def test_no_key_fails_explicitly(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': '', 'LLM_MODEL': ''}):
            with self.assertRaises(ModelUnavailable):
                LanguageModel().understand('hello', {}, [], [], [])

    def test_structured_call_and_no_provider_storage(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake-test-key', 'LLM_MODEL': 'test-model'}):
            with patch('leadpilot.llm.OpenAI') as client:
                expected = result(answers={'goal': 'Для работы'})
                client.return_value.responses.parse.return_value.output_parsed = expected
                actual = LanguageModel().understand('для работы', {}, [], [], [])
                self.assertEqual(actual.answers.goal, 'Для работы')
                call = client.return_value.responses.parse.call_args.kwargs
                self.assertFalse(call['store'])
                self.assertIn('text_format', call)

    def test_provider_exception_is_redacted(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake-test-key', 'LLM_MODEL': 'test-model'}):
            with patch('leadpilot.llm.OpenAI', side_effect=Exception('secret-token')):
                with self.assertRaises(ModelUnavailable) as error:
                    LanguageModel().understand('hello', {}, [], [], [])
                self.assertNotIn('secret-token', str(error.exception))
