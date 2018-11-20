from lxml import etree
from tornado.web import HTTPError

from frontik.handler import PageHandler


class Page(PageHandler):
    def prepare(self):
        super(Page, self).prepare()

        def pp(handler, cb):
            handler.add_header('X-Foo', 'Bar')
            cb()

        self.add_postprocessor(pp)

    def get_page(self):
        content_type = self.get_argument('type')

        def fail_page(_, __):
            raise HTTPError(500)

        self.post_url(self.request.host, self.request.path, callback=fail_page)

        if content_type == 'text':
            self.text = 'ok'
        elif content_type == 'json':
            self.json.put({'ok': True})
        elif content_type == 'xml':
            self.doc.put(etree.Element('ok'))
        elif content_type == 'xsl':
            self.doc.put(etree.Element('ok'))
            self.set_xsl('simple.xsl')

        self.finish_with_postprocessors()

    def post_page(self):
        pass
