# coding=utf-8

import unittest

from frontik.http_client import Upstream


class TestHttpConfigParser(unittest.TestCase):
    def test_single_server(self):
        config, servers = Upstream.parse_config(
            'tries=1 fail_timeout=1    max_fails=30 |server=172.17.0.1:2800     weight=100    | ')

        self.assertEquals('1', config['tries'])
        self.assertEquals('30', config['max_fails'])
        self.assertEquals('1', config['fail_timeout'])
        self.assertEquals(1, len(servers))

        server = servers[0]
        self.assertEquals('172.17.0.1:2800', server.address)
        self.assertEquals(100, server.weight)

    def test_single_server_without_last_separator(self):
        config, servers = Upstream.parse_config('|server=bla-bla   fail_timeout=1 max_fails=30 weight=1 ')

        self.assertEquals(0, len(config))
        self.assertEquals(1, len(servers))

        server = servers[0]
        self.assertEquals('bla-bla', server.address)
        self.assertEquals(1, server.weight)

    def test_multiple_servers(self):
        config, servers = Upstream.parse_config('|server=bla-bla weight=1 | server=someserver weight=2')

        self.assertEquals(0, len(config))
        self.assertEquals(2, len(servers))

        server = servers[0]
        self.assertEquals('bla-bla', server.address)
        self.assertEquals(1, server.weight)

        server = servers[1]
        self.assertEquals('someserver', server.address)
        self.assertEquals(2, server.weight)

    def test_defaults(self):
        config, servers = Upstream.parse_config('|server=bla-bla')

        self.assertEquals(0, len(config))
        self.assertEquals(1, len(servers))

        server = servers[0]
        self.assertEquals('bla-bla', server.address)
        self.assertEquals(1, server.weight)

    def test_ignoring_parameters(self):
        config, servers = Upstream.parse_config('abb=2|server=bla-bla some_parameter=434')

        self.assertEquals('2', config['abb'])
        self.assertEquals(1, len(servers))

        server = servers[0]
        self.assertEquals('bla-bla', server.address)
