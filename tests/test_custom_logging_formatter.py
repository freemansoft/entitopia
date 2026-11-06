"""Installing the coloured log formatter must not write to stdout.

Exists because it once printed the logger's repr, so every run, and even
`execute_project.py --help`, began with a stray `<RootLogger root (INFO)>`
line. Logs go to stderr; stdout is what a caller reads or pipes, so anything
written there by logging setup is noise in someone else's output.
"""

import logging

from utils.custom_logging_formatter import CustomFormatter


def test_installing_the_formatter_prints_nothing(capsys):
    logger = logging.getLogger("test-custom-formatter")
    CustomFormatter().replace_formatter(logger)
    assert capsys.readouterr().out == ""


def test_the_formatter_becomes_the_loggers_only_handler():
    logger = logging.getLogger("test-custom-formatter-handlers")
    logger.addHandler(logging.NullHandler())
    CustomFormatter().replace_formatter(logger)
    assert len(logger.handlers) == 1
    assert isinstance(logger.handlers[0].formatter, CustomFormatter)
