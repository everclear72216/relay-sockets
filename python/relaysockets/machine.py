
import asyncio
import logging

from exceptions import MachineDisconnectedException

class Machine(object):

    COMMAND_ACQUIRE = 'A'
    COMMAND_RELEASE = 'R'

    def __init__(self, reader, writer):
        super(Machine, self).__init__()

        self.id = ''

        self.reader = reader
        self.writer = writer

        self.acquired = False

        self.cmd_queue = asyncio.Queue()
        self.rsp_queue = asyncio.Queue()

        self.logger = logging.getLogger('machine')
        self.logger.debug('created machine')

    def close(self):
        self.writer.close()
        self.logger.info('machine connection closed')

    def get_id(self):
        return self.id

    def release_machine(self):
        self.cmd_queue.put_nowait(self.COMMAND_RELEASE)

    async def receive_command(self):
        try:
            command = await self.cmd_queue.get()
            return command

        except asyncio.CancelledError:
            self.logger.info('receive command canceled')
            raise

        except Exception:
            self.logger.exception('unknown error waiting for command')
            raise

    def send_response(self, response):
        self.rsp_queue.put_nowait(response)

    async def read(self, count):
        try:
            return await self.reader.read(count)

        except ConnectionError:
            self.logger.exception('reading from machine failed')
            return bytes()

        except asyncio.CancelledError:
            self.logger.info('reading from machine canceled')
            raise

    async def write(self, data):
        try:
            self.writer.write(data)
            await self.writer.drain()

        except ConnectionError:
            self.logger.exception('writing to machine failed')
            raise

        except asyncio.CancelledError:
            self.logger.exception('writing to machine canceled')
            raise

    async def obtain_id(self):
        data = await self.read(32)

        if data:
            self.logger.info('received identification')

            self.id =  data.decode('utf-8').strip()
            self.logger = logging.getLogger('machine[{}]'.format(self.id))

        else:
            self.logger.info('disconnected before identification')
            raise MachineDisconnectedException()

    async def acquire_machine(self):
        self.cmd_queue.put_nowait(self.COMMAND_ACQUIRE)
        result = await self.rsp_queue.get()
        return result

    async def wait_acquired(self):
        disconnected = False

        dummy_read = asyncio.get_event_loop().create_task(self.read(4096))
        check_cmds = asyncio.get_event_loop().create_task(self.cmd_queue.get())

        while not self.acquired:
            self.logger.debug('waiting for data or acquisition')

            done, pending = await asyncio.wait((dummy_read, check_cmds), return_when=asyncio.FIRST_COMPLETED)

            if dummy_read in done:
                result = dummy_read.result()

                if result:
                    dummy_read = asyncio.get_event_loop().create_task(self.read(4096))
                    self.logger.debug('received data; restarting read operation')

                else:
                    disconnected = True
                    check_cmds.cancel()
                    self.logger.debug('no data from machine; probably disconnected')

            if check_cmds in done:
                result = check_cmds.result()

                if result == self.COMMAND_ACQUIRE:
                    self.logger.debug('received acquisition command')

                    if not disconnected:
                        dummy_read.cancel()
                        self.acquired = True
                        self.rsp_queue.put_nowait(True)
                        self.logger.debug('acquisition granted')

                    else:
                        self.rsp_queue.put_nowait(False)
                        self.logger.debug('acquisition rejected')

                else:
                    self.logger.warn('release command received in wrong state; ignored')
                    check_cmds = asyncio.get_event_loop().create_task(self.cmd_queue.get())

            if disconnected:
                raise MachineDisconnectedException()

    async def wait_released(self):
        while self.acquired:
            cmd = await self.cmd_queue.get()

            if cmd == self.COMMAND_RELEASE:
                self.acquired = False

            else:
                self.logger.warn('acquire command received in wrong state')
