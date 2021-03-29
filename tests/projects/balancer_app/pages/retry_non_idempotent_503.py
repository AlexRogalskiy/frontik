from http_client import Upstream
from tornado.web import HTTPError

from frontik import media_types
from frontik.handler import PageHandler
from frontik.futures import AsyncGroup

from tests.projects.balancer_app import get_server
from tests.projects.balancer_app.pages import check_all_requests_done


class Page(PageHandler):
    def get_page(self):
        self.application.upstreams['retry_non_idempotent_503'] = Upstream('retry_non_idempotent_503',
                                                                          {'retry_policy': {
                                                                              503: {"idempotent": "true"}}},
                                                                          [get_server(self, 'broken'),
                                                                           get_server(self, 'normal')])

        def check_requests_cb():
            check_all_requests_done(self, 'retry_non_idempotent_503')

        async_group = AsyncGroup(check_requests_cb)

        def callback_post_with_retry(text, response):
            if response.error or text is None:
                raise HTTPError(500)

            self.text = text

        self.post_url('retry_non_idempotent_503', self.request.path,
                      callback=async_group.add(callback_post_with_retry))

    def post_page(self):
        self.add_header('Content-Type', media_types.TEXT_PLAIN)
        self.text = 'result'
