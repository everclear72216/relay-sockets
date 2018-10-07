
import json
import asyncio
import logging

from aiohttp import web
from aiohttp import WSMsgType

class WSStreamXTerm(object):
    def __init__(self, request):
        super(WSStreamXTerm, self).__init__()

        self.read_buffer = []
        self.write_buffer = []
        self.request = request
        self.websocket = web.WebSocketResponse()
        self.logger = logging.getLogger('WSStreamXTerm')

    def write(self, data, **kwargs):
        self.write_buffer.extend(list(data.decode('utf-8')))

    def close(self):
        loop = asyncio.get_event_loop()
        return loop.create_task(self.websocket.close())

    def get_websocket(self):
        return self.websocket

    async def start(self):
        self.logger.debug('starting websocket')
        await self.websocket.prepare(self.request)
        self.logger.debug('started websocket')

    async def read(self, count, **kwargs):
        output = []

        if len(self.read_buffer) < count:
            self.logger.debug('started reading from websocket')
            message = await self.websocket.receive()
            if message.type == WSMsgType.TEXT:
                self.logger.debug('received text message')
                payload = json.loads(message.data)
                self.read_buffer.extend(list(payload[1]))

            elif message.type == WSMsgType.BINARY:
                self.logger.debug('received binary message')

            elif message.type == WSMsgType.CLOSE:
                self.logger.debug('received close message')

            elif message.type == WSMsgType.CLOSED_FRAME:
                self.logger.debug('received closed frame')

            elif message.type == WSMsgType.ERROR:
                self.logger.debug('received error message')

            else:
                self.logger.error('received unexpected message type')

            for i in range(min(count, len(self.read_buffer))):
                output.append(self.read_buffer.pop(0))

        else:
            for i in range(count):
                output.append(self.read_buffer.pop(0))

        return ''.join(output).encode('utf-8')

    async def drain(self):
        payload = ['stdout', ''.join(self.write_buffer)]
        await self.websocket.send_json(payload)
        self.send_buffer = []
