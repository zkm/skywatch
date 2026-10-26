import logging
from dataclasses import replace

from app.config import Config
from app.source import KiwiSource, SourceState


def test_warns_about_kiwisdr_proxy_hosts(caplog):
    cfg = replace(Config(), kiwi_hosts=[("12345.proxy.kiwisdr.com", 8073), ("kiwi.example.org", 8073)])
    with caplog.at_level(logging.WARNING, logger="skywatch.source"):
        KiwiSource(cfg, SourceState())
    warned = [r.getMessage() for r in caplog.records]
    assert len(warned) == 1 and "12345.proxy.kiwisdr.com:8073" in warned[0]
