from tornado.web import HTTPError

from frontik.handler import PageHandler


class Page(PageHandler):
    def post_page(self):
        raise HTTPError(503, 'broken, retry')
