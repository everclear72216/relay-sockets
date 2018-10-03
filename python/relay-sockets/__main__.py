
import sys
import asyncio
import traceback

import logging
import logging.config

relays = list()
machines = list()
services = list()

class RelayException(BaseException):
    pass

class MachineDisconnectedException(RelayException):
    pass

class ServiceDisconnectedException(RelayException):
    pass

class Machine(object):
    def __init__(self, reader, writer):
        super(Machine, self).__init__()

        self.reader = reader
        self.writer = writer
        self.acquired = False
        self.identifier = None
        self.cmd_queue = asyncio.Queue()
        self.rsp_queue = asyncio.Queue()
        self.logger = logging.getLogger('Machine')

    def get_identifier(self):
        return self.identifier

    def get_reader(self):
        return self.reader

    def get_writer(self):
        return self.writer

    def close(self):
        self.writer.close()
        self.logger.info('Closed.')

    def release_machine(self):
        self.cmd_queue.put_nowait('R')

    async def obtain_identifier(self):
        data = await self.reader.read(32)
        if len(data) == 0:
            self.logger.info('Disconnected before identification.')
            raise MachineDisconnectedException()

        self.identifier =  data.decode('utf-8').strip()
        self.logger = logging.getLogger('Machine[{}]'.format(self.identifier))
        self.logger.info('Received identification.')

    async def acquire_machine(self):
        self.cmd_queue.put_nowait('A')
        result = await self.rsp_queue.get()
        return result

    async def wait_acquired(self):
        disconnected = False

        dummy_read = asyncio.get_event_loop().create_task(self.reader.read(1))
        check_cmds = asyncio.get_event_loop().create_task(self.cmd_queue.get())

        while not self.acquired:
            self.logger.debug('Waiting for data or acquisition.')
            done, pending = await asyncio.wait((dummy_read, check_cmds), return_when=asyncio.FIRST_COMPLETED)
            if dummy_read in done:
                result = dummy_read.result()
                if result:
                    dummy_read = asyncio.get_event_loop().create_task(self.reader.read(1))
                    self.logger.debug('Received data. Restarting read operation.')
                else:
                    disconnected = True
                    check_cmds.cancel()
                    self.logger.debug('No data from machine - probably disconnected.')

            if check_cmds in done:
                result = check_cmds.result()
                if result == 'A':
                    self.logger.debug('Received acquisition command.')
                    if not disconnected:
                        dummy_read.cancel()
                        self.acquired = True
                        self.rsp_queue.put_nowait(True)
                        self.logger.debug('Acquisition granted.')
                    else:
                        self.rsp_queue.put_nowait(False)
                        self.logger.debug('Acquisition rejected.')
                else:
                    self.logger.warn('Release command received in wrong state.')
                    self.logger.debug('Waiting for new command.')
                    check_cmds = asyncio.get_event_loop().create_task(self.cmd_queue.get())

            if disconnected:
                raise MachineDisconnectedException()

    async def wait_released(self):
        cmd = await self.cmd_queue.get()
        if cmd == 'R':
            self.acquired = False

class Service(object):
    def __init__(self, reader, writer, machines):
        super(Service, self).__init__()

        self.reader = reader
        self.writer = writer
        self.machines = machines
        self.logger = logging.getLogger('Service')
        self.machine_queue = asyncio.Queue(maxsize=1)

    def get_reader(self):
        return self.reader

    def get_writer(self):
        return self.writer

    def notify_machine(self):
        try:
            self.machine_queue.put_nowait('U')
        except asyncio.QueueFull:
            self.logger.debug('Ignoring QueueFull we need to update only once.')

        self.logger.info('Notified about machine update.')

    def close(self):
        self.writer.close()
        self.logger.info('Closed.')

    async def read(self, count, **kwargs):
        self.logger.debug('Started reading data.')

        data = await self.reader.read(count)
        decode = kwargs.get('decode', None)

        if decode:
            data = data.decode(decode)

        self.logger.debug('Read data: {}'.format(data))
        return data

    async def read_input(self):
        self.logger.debug('Started reading input.')
        data = bytearray()
        try:
            received = await self.reader.read(1)
            while received != bytes(b'\n') and received != bytes(b'\r'):
                if len(received) == 0:
                    self.logger.warning('No byte received - probably disconnected.')
                    return ''
                else:
                    self.logger.debug('Byte received.')
                    await self.write(received)
                    data.append(received[0])

                received = await self.reader.read(1)

            self.logger.debug('End-of-line received.')
            return data.decode('utf-8').strip()

        except ConnectionError:
            return ''

    async def write(self, data, **kwargs):
        self.logger.debug('Started writing data.')
        encode = kwargs.get('encode', None)
        
        if encode:
            data = data.encode(encode)

        self.writer.write(data)
        await self.writer.drain()

        self.logger.debug('Data written: {}'.format(data))

    async def wait_machine(self):
        self.logger.debug('Started waiting for machine.')
        event = await self.machine_queue.get()
        self.logger.debug('Received machine update.')
        return event

    async def list_machines(self):
        for idx, machine in enumerate(self.machines):
            await self.write('{}: {}\r\n'.format(idx, machine.get_identifier()), encode='utf-8')

    async def choose_machine(self):
        self.logger.info('Started choosing machine.')

        retries = 0
        machine = None
        self.machine_queue = asyncio.Queue(maxsize=0)

        while not machine:
            if retries:
                await self.write(b'\r\n\r\n')

            if self.machines:
                self.logger.debug('Presenting machines.')
                await self.list_machines()
                await self.write('Choose machine by index: ', encode='utf-8')
            else:
                self.logger.debug('Presenting no machines.')
                await self.list_machines()
                await self.write('Waiting for machine...\r\n', encode='utf-8')

            self.logger.debug('Waiting for machine or input.')
            message_task = asyncio.get_event_loop().create_task(self.read_input())
            machine_task = asyncio.get_event_loop().create_task(self.wait_machine())

            done, pending = await asyncio.wait((machine_task, message_task), return_when=asyncio.FIRST_COMPLETED)
            if machine_task in done:
                self.logger.debug('Machines changed.')
                if self.machines:
                    self.logger.debug('Machines available.')
                else:
                    self.logger.debug('No machines available.')

                retries += 1
                message_task.cancel()

            if message_task in done:
                if message_task.result():
                    if self.machines:
                        choice = int(message_task.result())
                        if choice < len(self.machines):
                            m = self.machines[choice]
                            if await m.acquire_machine():
                                machine = m
                                await self.write(b'\r\n')
                            else:
                                self.logger.info('Machine already acquired.')
                        else:
                            self.logger.debug('Client made invalid choice.')
                    else:
                        self.logger.debug('Client entered choice without machine.')
                        await self.write('Invalid choice!\r\n', encode='utf-8')
                else:
                    self.logger.debug('Client disconnected.')
                    raise ServiceDisconnectedException()

                machine_task.cancel()

        self.logger.info('Chose machine: {}'.format(machine.get_identifier()))
        return machine

class Relay(object):
    def __init__(self, machine, service):
        super(Relay, self).__init__()

        self.machine = machine
        self.service = service
        self.logger = logging.getLogger('Relay[{}]'.format(machine.get_identifier()))

    def get_machine(self):
        return self.machine

    def get_service(self):
        return self.service

    def write_machine(self, data):
        async def wm(data):
            try:
                self.machine.get_writer().write(data)
                await self.machine.get_writer().drain()
            except ConnectionError:
                self.logger.error('Writing to machine failed.')
                pass

        return asyncio.get_event_loop().create_task(wm(data))

    def write_service(self, data):
        async def ws(data):
            try:
                self.service.get_writer().write(data)
                await self.service.get_writer().drain()
            except ConnectionError:
                self.logger.error('Writing to service failed.')
                pass

        return asyncio.get_event_loop().create_task(ws(data))

    def read_machine(self):
        async def rm():
            try:
                data = await self.machine.get_reader().read(4096)
                return data
            except ConnectionError:
                self.logger.error('Reading from machine failed.')
                return bytes()

        return asyncio.get_event_loop().create_task(rm())

    def read_service(self):
        async def rs():
            try:
                data = await self.service.get_reader().read(4096)
                return data
            except ConnectionError:
                self.logger.error('Reading from service failed.')
                return bytes()

        return asyncio.get_event_loop().create_task(rs())

    async def do_relay(self):
        exc = None
        machine_write = None
        service_write = None

        try:
            await self.service.write('Relay established.\r\n', encode='utf-8')
        except ConnectionError:
            self.logger.error('Writing to service failed.')
            raise ServiceDisconnectedError()
        
        machine_read = self.read_machine()
        service_read = self.read_service()

        while True:
            done, pending = await asyncio.wait((machine_read, service_read), return_when=asyncio.FIRST_COMPLETED)
            if machine_read in done:
                result = machine_read.result()
                if result:
                    self.logger.debug('Received data from machine.')
                    self.logger.debug('Relaying data to service.')
                    service_write = self.write_service(result)
                    machine_read = self.read_machine()
                else:
                    self.logger.error('No data from machine - probably disconnected.')
                    service_read.cancel()

                    if machine_write:
                        machine_write.cancel()

                    if service_write:
                        service_write.cancel()

                    raise MachineDisconnectedException()

            if service_read in done:
                result = service_read.result()
                if result:
                    self.logger.debug('Received data from service.')
                    self.logger.debug('Relaying data to machine.')
                    machine_write = self.write_machine(result)
                    service_read = self.read_service()
                else:
                    self.logger.error('No data from service - probably disconnected.')
                    machine_read.cancel()

                    if machine_write:
                        machine_write.cancel()

                    if service_write:
                        service_write.cancel()

                    raise ServiceDisconnectedException()

async def handle_machine_client(reader, writer):
    logger = logging.getLogger('MachineClient')

    logger.info('Machine connected...')

    try:
        machine = Machine(reader, writer)
        await machine.obtain_identifier()
        machines.append(machine)

        while True:
            for service in services:
                service.notify_machine()

            await machine.wait_acquired()
            machines.pop(machines.index(machine))

            logger.info('Machine acquired.')

            for service in services:
                service.notify_machine()

            await machine.wait_released()
            machines.append(machine)

            logger.info('Machine released.')

    except MachineDisconnectedException:
        if machine in machines:
            machines.pop(machines.index(machine))

        for service in services:
            service.notify_machine()

    except:
        logger.exception('Unknown error')
        machine.close()

async def handle_service_client(reader, writer):
    logger = logging.getLogger('ServiceClient')

    logger.info('Client connected')

    service = Service(reader, writer, machines)
    services.append(service)

    while True:
        await asyncio.sleep(1.0)

        relay = None
        machine = None

        try:
            machine = await service.choose_machine()
            # no more notifications about new machines
            services.pop(services.index(service))

            # create and register relay
            logger.info('Establishing relay...')
            relay = Relay(machine, service)
            relays.append(relay)

            await relay.do_relay()

        except MachineDisconnectedException as machine_closed:
            logger.info('Machine disconnected.')

            # the relay does not exist anymore
            if relay:
                relays.pop(relays.index(relay))
            
            # the machine is no more
            machine.release_machine()

            # register service for machine notifications
            services.append(service)

        except ServiceDisconnectedException as service_closed:
            # register machine as being available again
            if machine:
                machine.release_machine()

            # the relay does not exist anymore
            if relay:
                relays.pop(relays.index(relay))

            # this service is done so terminate the handler
            break

        except Exception as exception:
            logger.exception('Unknown error.')
            
            # the relay does not exist anymore
            if relay:
                relays.pop(relays.index(relay))

            # the machine will be disconnected
            if machine:
                machine.realease_machine()

            # we are done, so terminate
            break

    # this service is no more
    if service in services:
        services.pop(services.index(service))

    service.close()

def main(argv):
    host = '192.168.0.231'
    machine_listen_port = 10000
    service_listen_port = 10001

    machine_server_coro = asyncio.start_server(handle_machine_client, host=host,
            port=machine_listen_port, reuse_address=True, reuse_port=True) 
    service_server_coro = asyncio.start_server(handle_service_client, host=host,
            port=service_listen_port, reuse_address=True, reuse_port=True)

    loop = asyncio.get_event_loop()

    machine_server = loop.run_until_complete(machine_server_coro)
    service_server = loop.run_until_complete(service_server_coro)

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass

    machine_server.close()
    loop.run_until_complete(machine_server.wait_closed())

    service_server.close()
    loop.run_until_complete(service_server.wait_closed())

    loop.close()

if __name__ == '__main__':

    logging.config.dictConfig({
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'standard' : {
                'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
            },
        },
        'handlers': {
            'def': {
                'level': 'DEBUG',
                'class': 'logging.StreamHandler',
                'stream': 'ext://sys.stdout',
                'formatter': 'standard'
            },
        },
        'loggers': {
            '': {
                'handlers': ['def'],
                'level': 'DEBUG',
                'propagate': True
            }
        },
        'root': {
            'level': 'DEBUG',
            'handlers': ['def']
        }
    })

    logger = logging.getLogger(__name__)
    logger.info('Socket Relay Started.')

    main(sys.argv)
