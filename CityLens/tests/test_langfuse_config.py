import json
import sys

import pytest

from scripts.check_langfuse import load_env, main
from scripts.check_langfuse import verify_delivery
from types import SimpleNamespace


def test_reads_only_langfuse_settings_and_handles_quoted_values(tmp_path):
    path = tmp_path/'settings.env'
    path.write_text('DEEPSEEK_API_KEY=private-model-key\nLANGFUSE_SECRET_KEY="private-lf-key"\n', encoding='utf-8')
    assert load_env(path) == {'LANGFUSE_SECRET_KEY': 'private-lf-key'}


def test_duplicate_configuration_rejected_without_echoing_values(tmp_path):
    path = tmp_path/'settings.env'
    path.write_text('LANGFUSE_SECRET_KEY=private-1\nLANGFUSE_SECRET_KEY=private-2\n', encoding='utf-8')
    with pytest.raises(ValueError) as error:
        load_env(path)
    assert 'private-' not in str(error.value)


def test_presence_check_does_not_initialize_client_or_print_secrets(tmp_path, monkeypatch, capsys):
    from citylens import observability
    def forbidden():
        raise AssertionError('Must not initialize SDK or send network requests')
    monkeypatch.setattr(observability, 'initialize', forbidden)
    path = tmp_path/'settings.env'
    path.write_text('CITYLENS_LANGFUSE_ENABLED=true\nLANGFUSE_PUBLIC_KEY=private-public-value\n'
                    'LANGFUSE_SECRET_KEY=private-secret-value\nLANGFUSE_BASE_URL=https://cloud.langfuse.com\n', encoding='utf-8')
    for key in ['LANGFUSE_PUBLIC_KEY', 'LANGFUSE_SECRET_KEY', 'LANGFUSE_BASE_URL', 'CITYLENS_LANGFUSE_ENABLED']:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sys, 'argv', ['check_langfuse.py', '--env-file', str(path)])
    assert main() == 0
    output = capsys.readouterr().out
    assert 'private-public-value' not in output and 'private-secret-value' not in output
    assert json.loads(output)['network_requests_sent'] == 0


def test_delivery_requires_all_four_cloud_observations():
    seen = []
    def get_many(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(name=n) for n in
            ['citylens.workflow', 'citylens.model', 'citylens.tool', 'citylens.connection_check']])
    client = SimpleNamespace(api=SimpleNamespace(observations=SimpleNamespace(get_many=get_many)))
    assert verify_delivery(client, 'test-trace')['cloud_delivery_verified'] is True
    assert seen == [{'trace_id': 'test-trace', 'limit': 20}]


def test_empty_cloud_response_never_counts_as_delivery(monkeypatch):
    import scripts.check_langfuse as check
    monkeypatch.setattr(check.time, 'sleep', lambda seconds: None)
    client = SimpleNamespace(api=SimpleNamespace(observations=SimpleNamespace(
        get_many=lambda **kwargs: SimpleNamespace(data=[]))))
    assert verify_delivery(client, 'test-trace')['cloud_delivery_verified'] is False
