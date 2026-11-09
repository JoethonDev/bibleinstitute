#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

try:
    from django.core.management import execute_from_command_line
except ImportError as exc:
    _DJANGO_IMPORT_ERROR = exc
    execute_from_command_line = None
else:
    _DJANGO_IMPORT_ERROR = None


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'learning_platform.settings')
    if execute_from_command_line is None:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from _DJANGO_IMPORT_ERROR
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
