
import asyncio
import logging

from aiohttp import web

from relay import Relay
from machine import Machine
from service import Service

from wsstreamxterm import WSStreamXTerm

from exceptions import MachineDisconnectedException
from exceptions import ServiceDisconnectedException

class RelayServer(object):

    default_config = {
        'listen_interface_ip': '127.0.0.1',
        'machine': {
            'port': 10000,
            'reuse_port': True,
            'reuse_address': True
        },
        'service': {
            'port': 10001,
            'reuse_port': True,
            'reuse_address': True
        },
        'http': {
            'port': 8080
        }
    }

    def __init__(self, **kwargs):
        super(RelayServer, self).__init__()

        self.machines = []
        self.services = []

        self.machine_server = None
        self.service_server = None

        self.logger = logging.getLogger('relayserver')
        self.config = kwargs.get('config', self.default_config)

        self.logger.debug('created relay server')

    def notify_machine(self):
        for service in self.services:
            service.notify_machine()

    def add_machine(self, machine):
        if machine not in self.machines:
            self.machines.append(machine)
            self.notify_machine()

    def remove_machine(self, machine):
        if machine in self.machines:
            self.machines.pop(self.machines.index(machine))
            self.notify_machine()

    def add_service(self, service):
        if service not in self.services:
            self.services.append(service)

    def remove_service(self, service):
        if service in self.services:
            self.services.pop(self.services.index(service))

    async def start(self):
        self.logger.info('starting relay server')

        try:
            web_app = web.Application()
            web_app.add_routes([
                web.static('/app', self.config['http']['home']),
                web.get('/websocket', self.websocket_connected)
            ])

            web_runner = web.AppRunner(web_app)
            await web_runner.setup()

            web_site = web.TCPSite(web_runner,
                self.config['listen_interface_ip'],
                self.config['http']['port'])

            await web_site.start()
            self.logger.debug('relay website started')

            await asyncio.start_server(self.machine_connected,
                port=int(self.config['machine']['port']),
                host=str(self.config['listen_interface_ip']),
                reuse_port=bool(self.config['machine']['reuse_port']),
                reuse_address=bool(self.config['machine']['reuse_address']))
            self.logger.debug('machine server started')

            await asyncio.start_server(self.service_connected,
                port=int(self.config['service']['port']),
                host=str(self.config['listen_interface_ip']),
                reuse_port=bool(self.config['service']['reuse_port']),
                reuse_address=bool(self.config['service']['reuse_address']))
            self.logger.debug('service server started')

            self.logger.info('relay server started')

        except asyncio.CancelledError:
            self.logger.info('start relay server cancelled')

            for task in tasks:
                task.cancel()

            raise

        except Exception:
            self.logger.exception('unknown error starting relay server')
            raise

    async def stop(self):
        pass

    async def websocket_connected(self, request):
        self.logger.info('websocket connected')

        try:
            self.logger.debug('creating websocket stream')
            stream = WSStreamXTerm(request)

            self.logger.debug('starting websocket stream')
            await stream.start()

            self.logger.debug('handling websocket as service')
            await self.service_connected(stream, stream)
            
        except Exception:
            self.logger.exception('error handling websocket')
            raise

        return stream.get_websocket()

    async def machine_connected(self, reader, writer):
        self.logger.info('machine connected')

        machine = Machine(reader, writer)

        try:
            await machine.obtain_id()
            self.add_machine(machine)

            while True:
                await machine.wait_acquired()
                self.logger.info('machine acquired')
                self.remove_machine(machine)

                await machine.wait_released()
                self.logger.info('machine released')
                self.add_machine(machine)

        except MachineDisconnectedException:
            self.remove_machine(machine)
            self.notify_machine()

        except:
            self.logger.exception('unknown machine error')
            machine.close()

    async def service_connected(self, reader, writer):
        self.logger.info('service connected')

        service = Service(reader, writer)
        self.add_service(service)

        while True:
            await asyncio.sleep(1.0)

            machine = None

            try:
                machine = await service.choose_machine(self.machines)

                self.remove_service(service)
                relay = Relay(machine, service)
                self.logger.info('relay established')

                await relay.do_relay()

            except MachineDisconnectedException as machine_closed:
                self.logger.info('machine disconnected')

                machine.release_machine()

                self.add_service(service)

            except ServiceDisconnectedException as service_closed:
                self.logger.info('service disconnected')

                if machine:
                    machine.release_machine()

                break

            except Exception as exception:
                self.logger.exception('unknown service error')

                if machine:
                    machine.release_machine()

                break

        self.remove_service(service)

        service.close()