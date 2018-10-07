
import asyncio
import logging

from exceptions import ServiceDisconnectedException

class Service(object):
    def __init__(self, reader, writer):
        super(Service, self).__init__()

        self.reader = reader
        self.writer = writer
        self.logger = logging.getLogger('Service')
        self.machine_queue = asyncio.Queue(maxsize=1)

    def close(self):
        self.logger.info('service connection closed')

        self.writer.close()

    def notify_machine(self):
        self.logger.info('machine update received')

        try:
            self.machine_queue.put_nowait(None)

        except asyncio.QueueFull:
            pass

    async def read(self, count, **kwargs):
        self.logger.debug('reading from service')

        try:
            data = await self.reader.read(count)
            decode = kwargs.get('decode', None)

            if decode:
                data = data.decode(decode)

            self.logger.debug('read data: {}'.format(data))
            return data

        except ConnectionError:
            self.logger.exception('reading from service failed')
            return byte()

        except asyncio.CancelledError:
            self.logger.info('reading from service canceled')
            raise

    async def read_input(self):
        self.logger.debug('reading input')

        data = bytearray()

        try:
            received = await self.read(1)

            while received != bytes(b'\n') and received != bytes(b'\r'):

                if 0 == len(received):
                    self.logger.warning('no byte received; disconnected')
                    return ''

                else:
                    self.logger.debug('byte received')
                    await self.write(received)
                    data.append(received[0])

                received = await self.read(1)

            self.logger.debug('end-of-line received')
            return data.decode('utf-8').strip()

        except ConnectionError:
            self.logger.exception('reading from service failed')
            return ''

        except asyncio.CancelledError:
            self.logger.exception('reading from service canceled')
            raise

    async def write(self, data, **kwargs):
        self.logger.debug('writing to service')

        encode = kwargs.get('encode', None)

        if encode:
            data = data.encode(encode)

        try:
            self.writer.write(data)
            await self.writer.drain()

            self.logger.debug('data written: {}'.format(data))

        except ConnectionError:
            self.logger.exception('writing to service failed')
            raise

        except asyncio.CancelledError:
            self.logger.info('writing to service canceled')
            raise

    async def wait_machine(self):
        self.logger.debug('started waiting for machine')
        event = await self.machine_queue.get()
        self.logger.debug('received machine update')
        return event

    async def list_machines(self, machines):
        for idx, machine in enumerate(machines):
            await self.write('{}: {}\r\n'.format(idx, machine.get_id()), encode='utf-8')

    async def choose_machine(self, machines):
        self.logger.info('started choosing machine')

        retries = 0
        machine = None
        self.machine_queue = asyncio.Queue(maxsize=0)

        while not machine:
            if retries:
                await self.write(b'\r\n\r\n')

            if machines:
                self.logger.debug('presenting machines')
                await self.list_machines(machines)
                await self.write('Choose machine by index: ', encode='utf-8')
            else:
                self.logger.debug('presenting no machines')
                await self.write('Waiting for machine...\r\n', encode='utf-8')

            self.logger.debug('waiting for machine or input')
            message_task = asyncio.get_event_loop().create_task(self.read_input())
            machine_task = asyncio.get_event_loop().create_task(self.wait_machine())

            tasks = [message_task, machine_task]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

            if machine_task in done:
                self.logger.debug('machines changed')
                if machines:
                    self.logger.debug('machines available')
                else:
                    self.logger.debug('no machines available')

                retries += 1
                message_task.cancel()

            if message_task in done:
                result = message_task.result()

                if result:
                    choice = int(result)

                    if machines:
                        if choice < len(machines):
                            m = machines[choice]

                            if await m.acquire_machine():
                                machine = m
                                await self.write(b'\r\n')
                            else:
                                self.logger.info('machine already acquired')

                        else:
                            self.logger.debug('client made invalid choice')

                    else:
                        self.logger.debug('client entered choice without machine')
                        await self.write('Invalid choice!\r\n', encode='utf-8')

                else:
                    self.logger.debug('client disconnected')
                    raise ServiceDisconnectedException()

                machine_task.cancel()

        self.logger.info('chose machine: {}'.format(machine.get_id()))
        return machine
