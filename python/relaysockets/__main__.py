
import os
import sys
import yaml
import asyncio

import logging
import logging.config

from relayserver import RelayServer

def main(argv):

    config_file_name = 'config.yml'

    if os.path.exists(config_file_name):
        with open(config_file_name, 'r') as ymlconfig:
            config = yaml.load(ymlconfig)
    else:
        config = {}

    if config.get('logging', False):
        logging.config.dictConfig(config['logging'])
    else:
        logging.basicConfig(level=logging.INFO)

    logger = logging.getLogger('main')
    logger.info('logging initialized')

    if config.get('relayserver', False):
        server = RelayServer(config = config['relayserver'])
    else:
        server = RelayServer()

    server_task = asyncio.get_event_loop().create_task(server.start())
    logger.info('created server task')

    try:
        logger.info('running server')
        asyncio.get_event_loop().run_forever()
    except KeyboardInterrupt:
        logger.info('keyboard interrupt')
        pass

    asyncio.get_event_loop().close()

if __name__ == '__main__':
    main(sys.argv)
