#!/usr/bin/env python3
import logging
import sys

from os_ken.lib import hub
hub.patch(thread=False)

from os_ken import cfg
from os_ken import log
from os_ken.base.app_manager import AppManager

log.early_init_log(logging.INFO)
CONF = cfg.CONF
CONF(args=[], project='osken', version='osken-manager')

def main():
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python3 run_controller.py <app_module>')
    
    app_lists = ['os_ken.controller.ofp_handler'] + sys.argv[1:]
    try:
        AppManager.run_apps(app_lists)
    except (KeyboardInterrupt, AttributeError) as e:
        if isinstance(e, KeyboardInterrupt) or "'kill'" in str(e):
            print('\nController stopped.')
        else:
            raise

if __name__ == '__main__':
    main()