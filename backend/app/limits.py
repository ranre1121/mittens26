"""Limit the actual multipart body before Starlette spools it to disk."""
from starlette.responses import JSONResponse
from starlette.exceptions import HTTPException


class BodyTooLarge(HTTPException):
    def __init__(self):
        super().__init__(413, "Upload too large")


class UploadLimitMiddleware:
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST':
            return await self.app(scope, receive, send)
        received = 0
        exceeded = False
        replaced = False

        async def bounded_receive():
            nonlocal received, exceeded
            message = await receive()
            received += len(message.get('body', b''))
            if received > self.limit:
                exceeded = True
                raise BodyTooLarge()
            return message
        async def bounded_send(message):
            nonlocal replaced
            if exceeded:
                payload = b'{"detail":"Upload too large"}'
                if message['type'] == 'http.response.start':
                    message = {**message, 'status': 413, 'headers': [(b'content-type', b'application/json'), (b'content-length', str(len(payload)).encode())]}
                elif message['type'] == 'http.response.body':
                    if replaced:
                        return
                    message = {**message, 'body': payload, 'more_body': False}
                    replaced = True
            await send(message)
        try:
            await self.app(scope, bounded_receive, bounded_send)
        except BodyTooLarge:
            await JSONResponse({'detail': 'Upload too large'}, status_code=413)(scope, receive, send)
