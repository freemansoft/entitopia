import logging

# came from https://stackoverflow.com/questions/384076/how-can-i-color-python-logging-output


class CustomFormatter(logging.Formatter):
    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    white = "\x1b[37m;20m"
    light_purple = "\x1b[94m"
    reset = "\x1b[0m"
    format = "%(asctime)s - %(name)30s - %(levelname)8s - %(message)s (%(filename)s:%(lineno)d)"

    # https://talyian.github.io/ansicolors/
    FORMATS = {
        logging.DEBUG: light_purple + format + reset,
        logging.INFO: grey + format + reset,
        logging.WARNING: yellow + format + reset,
        logging.ERROR: red + format + reset,
        logging.CRITICAL: bold_red + format + reset,
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt)
        return formatter.format(record)

    def replace_formatter(self, root_logger):
        """
        replace the current formatters with this one
        logger is the where we want the formatter to start, usually the root logger
        """

        print(root_logger)
        # create console handler with a higher log level
        handler = logging.StreamHandler()
        handler.setFormatter(CustomFormatter())
        root_logger.handlers = []
        root_logger.addHandler(handler)
