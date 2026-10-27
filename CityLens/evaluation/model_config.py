"""Explicit evaluation-only credential loading. No environment mutation or API calls."""
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_FILE = Path(__file__).resolve().parents[1] / '.env'
ALLOWED = {'CITYLENS_LLM_PROVIDER', 'CITYLENS_LLM_MODEL', 'DEEPSEEK_API_KEY',
           'CITYLENS_LLM_API_KEY', 'CITYLENS_LLM_BASE_URL', 'CITYLENS_LLM_TIMEOUT'}


@dataclass(frozen=True)
class DeepSeekSettings:
    model: str
    api_key: str = field(repr=False)
    base_url: str = 'https://api.deepseek.com'
    timeout: float = 12.0

    def provider_config(self):
        from citylens.llm import LLMConfig
        return LLMConfig(provider='deepseek', model=self.model, api_key=self.api_key,
                         base_url=self.base_url, timeout=self.timeout)


def read_settings(path=DEFAULT_FILE):
    path = Path(path)
    if not path.exists():
        raise ValueError('Experiment .env is missing; copy evaluation/deepseek.env.example')
    values = {}
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        key = key.strip()
        if key not in ALLOWED:
            continue  # Stable .env also contains Java/storage/observability settings.
        if not separator or key in values:
            raise ValueError('Model .env contains unknown, malformed or duplicate settings')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        values[key] = value
    if values.get('CITYLENS_LLM_PROVIDER', 'deepseek') != 'deepseek':
        raise ValueError('This evaluation configuration only supports DeepSeek')
    endpoint = values.get('CITYLENS_LLM_BASE_URL', 'https://api.deepseek.com').rstrip('/')
    if endpoint not in {'https://api.deepseek.com', 'https://api.deepseek.com/v1'}:
        raise ValueError('Use the official DeepSeek HTTPS endpoint for this evaluation profile')
    if values.get('DEEPSEEK_API_KEY') and values.get('CITYLENS_LLM_API_KEY'):
        raise ValueError('Set only one of DEEPSEEK_API_KEY / CITYLENS_LLM_API_KEY')
    return values


def load_settings(path=DEFAULT_FILE):
    values = read_settings(path)
    key = values.get('DEEPSEEK_API_KEY') or values.get('CITYLENS_LLM_API_KEY') or ''
    model = values.get('CITYLENS_LLM_MODEL', '')
    if not key or not model:
        raise ValueError('Fill the API key and model ID in the experiment .env; no API request was sent')
    try:
        timeout = float(values.get('CITYLENS_LLM_TIMEOUT', '12'))
    except ValueError:
        raise ValueError('Model timeout must be numeric') from None
    if not 1 <= timeout <= 30:
        raise ValueError('Model timeout must be between 1 and 30 seconds')
    return DeepSeekSettings(model, key, values.get('CITYLENS_LLM_BASE_URL', 'https://api.deepseek.com').rstrip('/'), timeout)


def configuration_status():
    values = read_settings()
    return {'kind': 'configuration_presence_only',
            'key_present': bool(values.get('DEEPSEEK_API_KEY') or values.get('CITYLENS_LLM_API_KEY')),
            'model_present': bool(values.get('CITYLENS_LLM_MODEL')),
            'connectivity_verified': False, 'model_requests_sent': 0,
            'lab_workbench_mode': 'rules', 'evaluation_adapter': 'asgi-mock-v1'}
