import json
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')


def env(name, default=''):
    return os.getenv(name, default)


def knowledge():
    return json.loads((ROOT / 'knowledge.json').read_text(encoding='utf-8'))


def catalog():
    return json.loads((ROOT / 'catalog.json').read_text(encoding='utf-8'))['products']
