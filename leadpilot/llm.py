import json
from typing import Literal
from pydantic import BaseModel, ConfigDict
from openai import OpenAI
from .config import env
from .prompts import SYSTEM


class Answers(BaseModel):
    model_config = ConfigDict(extra='forbid')
    goal: str | None
    problem: str | None
    level: str | None
    timing: str | None
    format: str | None
    budget: str | None
    decision: str | None


class Understanding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    intent: Literal['answer', 'recommend', 'product', 'faq', 'manager', 'complaint', 'objection', 'unknown', 'identity']
    answers: Answers
    product_id: str | None
    faq_id: str | None
    objection: Literal['', 'price', 'think', 'time', 'failed', 'consult', 'compare']
    valid: bool


class ModelUnavailable(Exception):
    pass


class LanguageModel:
    @property
    def configured(self):
        return bool(env('OPENAI_API_KEY') and env('LLM_MODEL'))

    def understand(self, text, state, products, faq, history):
        if not self.configured:
            raise ModelUnavailable('LLM is not configured')
        try:
            client = OpenAI(api_key=env('OPENAI_API_KEY'),
                            base_url=env('OPENAI_BASE_URL', 'https://api.openai.com/v1'),
                            timeout=18, max_retries=0)
            result = client.responses.parse(
                model=env('LLM_MODEL'), store=False, instructions=SYSTEM,
                input=json.dumps({'message': text, 'state': state, 'eligible_products': products,
                                  'faq': faq, 'recent_history': history[-12:]}, ensure_ascii=False),
                text_format=Understanding)
            if result.output_parsed is None:
                raise ModelUnavailable('No structured result')
            return result.output_parsed
        except ModelUnavailable:
            raise
        except Exception:
            raise ModelUnavailable('LLM request failed') from None

    def transcribe(self, content, filename='voice.ogg'):
        if not self.configured:
            raise ModelUnavailable('LLM is not configured')
        try:
            client = OpenAI(api_key=env('OPENAI_API_KEY'),
                            base_url=env('OPENAI_BASE_URL', 'https://api.openai.com/v1'),
                            timeout=18, max_retries=0)
            return client.audio.transcriptions.create(
                model=env('TRANSCRIPTION_MODEL', 'whisper-1'), file=(filename, content)).text
        except Exception:
            raise ModelUnavailable('Transcription failed') from None
