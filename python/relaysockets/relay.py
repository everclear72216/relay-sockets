
import asyncio
import logging

from exceptions import MachineDisconnectedException
from exceptions import ServiceDisconnectedException

class Relay(object):

    READ_CHUNK_SIZE = 4096

    def __init__(self, machine, service):
        super(Relay, self).__init__()

        self.machine = machine
        self.service = service
        self.logger = logging.getLogger('relay[{}]'.format(machine.get_id()))

    def write_machine(self, data):
        return asyncio.get_event_loop().create_task(self.machine.write(data))

    def write_service(self, data):
        return asyncio.get_event_loop().create_task(self.service.write(data))

    def read_machine(self):
        return asyncio.get_event_loop().create_task(self.machine.read(self.READ_CHUNK_SIZE))

    def read_service(self):
        return asyncio.get_event_loop().create_task(self.service.read(self.READ_CHUNK_SIZE))

    async def do_relay(self):
        machine_write = None
        service_write = None

        try:
            await self.service.write('relay established\r\n', encode='latin-1')

        except ConnectionError:
            self.logger.error('writing to service failed')
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