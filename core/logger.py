import logging
import os
from config.settings import Settings


class Logger:
    LOG_DIR = Settings.LOG_DIR
    os.makedirs(LOG_DIR, exist_ok=True)
    _loggers = {}

    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        if name in cls._loggers:
            return cls._loggers[name]

        logger = logging.getLogger(name)
        logger.setLevel(getattr(logging, Settings.LOG_LEVEL))

        log_file = os.path.join(cls.LOG_DIR, f'{name}.log')
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)

        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        formatter = logging.Formatter(
            '%(asctime)s | %(name)s | %(levelname)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

        cls._loggers[name] = logger
        return logger