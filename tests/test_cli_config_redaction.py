from __future__ import annotations

from argparse import Namespace

from ackstreet.cli import cmd_config
from ackstreet.config import Config


def test_config_show_redacts_secrets(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ACKSTREET_HOME", str(tmp_path))
    cfg = Config.load()
    cfg.set("providers", "custom.api_key_env", "CUSTOM_API_KEY")
    cfg.set("connectors", "telegram.bot_token", "123:secret")
    cfg.save()

    args = Namespace(
        command="config",
        config=None,
        home=str(tmp_path),
        provider=None,
        model=None,
        approval_mode=None,
        config_action="show",
    )
    assert cmd_config(args) == 0
    out = capsys.readouterr().out
    assert "123:secret" not in out
    assert 'bot_token = "***"' in out
