"""Run local browser demo: python run.py."""
import os
import uvicorn
from leadpilot.config import env


if __name__ == '__main__':
    uvicorn.run('leadpilot.app:app', host='127.0.0.1', port=int(env('PORT', '8000')))
