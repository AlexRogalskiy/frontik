from frontik.handler import JsonPageHandler
from frontik.preprocessors import preprocessor


@preprocessor
async def pp1(handler):
    def _cb(_, __):
        handler.future_result = 'test'

    handler.future = handler.add_preprocessor_future(
        handler.post_url(handler.request.host, handler.request.uri, callback=_cb)
    )


@preprocessor
async def pp2(handler):
    await handler.future
    handler.json.put({
        'test': handler.future_result
    })


class Page(JsonPageHandler):
    @pp1
    @pp2
    async def get_page(self):
        pass

    async def post_page(self):
        pass
