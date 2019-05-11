import asyncio

from tornado.web import HTTPError

from frontik import media_types
from frontik.handler import PageHandler

from tests.projects.balancer_app import get_server
from tests.projects.balancer_app.pages import check_all_requests_done


class Page(PageHandler):
    async def get_page(self):
        self.application.http_client_factory.register_upstream(
            'retry_non_idempotent_503', {'retry_policy': 'non_idempotent_503'},
            [get_server(self, 'broken'), get_server(self, 'normal')]
        )
        self.application.http_client_factory.register_upstream(
            'do_not_retry_non_idempotent_503', {}, [get_server(self, 'broken'), get_server(self, 'normal')]
        )

        def callback_post_without_retry(_, response):
            if response.code != 503:
                raise HTTPError(500)

        def callback_post_with_retry(text, response):
            if response.error or text is None:
                raise HTTPError(500)

            self.text = text

        await asyncio.gather(
            self.post_url('retry_non_idempotent_503', self.request.path, callback=callback_post_with_retry),
            self.post_url('do_not_retry_non_idempotent_503', self.request.path, callback=callback_post_without_retry)
        )

        check_all_requests_done(self, 'retry_non_idempotent_503')
        check_all_requests_done(self, 'do_not_retry_non_idempotent_503')

    async def post_page(self):
        self.add_header('Content-Type', media_types.TEXT_PLAIN)
        self.text = 'result'
