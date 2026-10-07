"""Run the new dashboard from any working directory: python demo_web_pi.py."""
import sys
from signal_demo.__main__ import main


if __name__ == '__main__':
    sys.argv.insert(1, 'web')
    main()
