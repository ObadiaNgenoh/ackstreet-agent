"""Allow ``python -m ackstreet`` as an alternative to the ``ackstreet`` script."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
