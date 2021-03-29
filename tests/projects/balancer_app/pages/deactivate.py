from http_client import Upstream

import frontik.handler
from tornado.ioloop import IOLoop

from tests.projects.balancer_app import get_server
from tests.projects.balancer_app.pages import check_all_requests_done, check_all_servers_occupied


class Page(frontik.handler.PageHandler):
    def get_page(self):
        server = get_server(self, 'free')
        self.application.upstreams['deactivate'] = Upstream(
            'deactivate', {'max_fails': 1, 'fail_timeout_sec': 0.1}, [server])
        self.text = ''

        def check_server_active():
            server_stat = self.application.http_client_factory.server_statistics.get(server.address)
            if server_stat.requests > 0:
                self.text += ' activated'

            check_all_requests_done(self, 'deactivate')

        def callback_post(_, response):
            if response.error and response.code == 502:
                self.text = 'deactivated'

            self.add_timeout(IOLoop.current().time() + 0.2,
                             self.finish_group.add(self.check_finished(check_server_active)))

        self.post_url('deactivate', self.request.path, callback=callback_post)

        check_all_servers_occupied(self, 'deactivate')
