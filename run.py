"""Run the pipeline from the command line: python run.py [--help]"""

import sys

from app.cli import main

if __name__ == "__main__":
    sys.exit(main())
