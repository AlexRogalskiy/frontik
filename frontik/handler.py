# coding=utf-8

import base64
from functools import partial
import time

import tornado.curl_httpclient
import tornado.httputil
from tornado.ioloop import IOLoop
import tornado.options
import tornado.web

import frontik.auth
import frontik.handler_active_limit
import frontik.producers.json_producer
import frontik.producers.xml_producer
import frontik.util
from frontik.async import AsyncGroup
from frontik.compat import iteritems
from frontik.handler_debug import PageHandlerDebug
from frontik.http_client import HttpClient
from frontik.http_codes import process_status_code
from frontik.request_context import RequestContext


class HTTPError(tornado.web.HTTPError):
    """
    Extends tornado.web.HTTPError with several keyword-only arguments.
    Also allow using some extended HTTP codes

    :arg dict headers: Custom HTTP headers to pass along with the error response.
    :arg string text: Plain text override for error response.
    :arg etree xml: XML node to be added to `self.doc`. If present, error page will be
        produced with `application/xml` content type.
    :arg dict json: JSON dict to be used as error response. If present, error page
        will be produced with `application/json` content type.
    """
    def __init__(self, status_code, log_message=None, *args, **kwargs):
        headers = kwargs.pop('headers', {})
        for data in ('text', 'xml', 'json'):
            setattr(self, data, kwargs.pop(data, None))

        status_code, kwargs['reason'] = process_status_code(status_code, kwargs.get('reason'))
        super(HTTPError, self).__init__(status_code, log_message, *args, **kwargs)
        self.headers = headers


class BaseHandler(tornado.web.RequestHandler):

    preprocessors = ()

    # to restore tornado.web.RequestHandler compatibility
    def __init__(self, application, request, logger, **kwargs):
        self._prepared = False
        self.name = self.__class__.__name__
        self.request_id = logger.request_id
        self.config = application.config

        self.log = logger
        self._exception_hooks = []

        for initializer in application.loggers_initializers:
            initializer(self)

        super(BaseHandler, self).__init__(application, request, logger=self.log, **kwargs)

        self.log.register_page_handler(self)
        self._debug_access = None

        self._template_postprocessors = []
        self._early_postprocessors = []
        self._returned_methods = set()

        self._http_client = HttpClient(self, self.application.curl_http_client, self.modify_http_client_request)

        self.text = None

    def __repr__(self):
        return '.'.join([self.__module__, self.__class__.__name__])

    def initialize(self, logger=None, **kwargs):
        # Hides logger keyword argument from incompatible tornado versions
        super(BaseHandler, self).initialize(**kwargs)

    def prepare(self):
        self.active_limit = frontik.handler_active_limit.PageHandlerActiveLimit(self)
        self.debug = PageHandlerDebug(self)
        self.finish_group = AsyncGroup(self.check_finished(self._finish_page_cb), name='finish', logger=self.log)

        self.json_producer = self.application.json.get_producer(self)
        self.json = self.json_producer.json

        self.xml_producer = self.application.xml.get_producer(self)
        self.xml = self.xml_producer  # deprecated synonym
        self.doc = self.xml_producer.doc

        self._prepared = True

    def require_debug_access(self, login=None, passwd=None):
        if self._debug_access is None:
            if tornado.options.options.debug:
                debug_access = True
            else:
                check_login = login if login is not None else tornado.options.options.debug_login
                check_passwd = passwd if passwd is not None else tornado.options.options.debug_password
                error = frontik.auth.check_debug_auth(self, check_login, check_passwd)
                debug_access = (error is None)
                if not debug_access:
                    code, headers = error
                    raise HTTPError(code, headers=headers)

            self._debug_access = debug_access

    def set_default_headers(self):
        self._headers = tornado.httputil.HTTPHeaders({
            'Server': 'Frontik/{0}'.format(frontik.version),
            'X-Request-Id': self.request_id,
        })

    def decode_argument(self, value, name=None):
        try:
            return super(BaseHandler, self).decode_argument(value, name)
        except (UnicodeError, tornado.web.HTTPError):
            self.log.warning('cannot decode utf-8 query parameter, trying other charsets')

        try:
            return frontik.util.decode_string_from_charset(value)
        except UnicodeError:
            self.log.exception('cannot decode argument, ignoring invalid chars')
            return value.decode('utf-8', 'ignore')

    def set_status(self, status_code, reason=None):
        status_code, reason = process_status_code(status_code, reason)
        super(BaseHandler, self).set_status(status_code, reason=reason)

    def redirect(self, url, *args, **kwargs):
        self.log.info('redirecting to: %s', url)
        return super(BaseHandler, self).redirect(url, *args, **kwargs)

    @staticmethod
    def add_callback(callback):
        IOLoop.current().add_callback(callback)

    @staticmethod
    def add_timeout(deadline, callback):
        return IOLoop.current().add_timeout(deadline, callback)

    @staticmethod
    def remove_timeout(timeout):
        IOLoop.current().remove_timeout(timeout)

    @staticmethod
    def add_future(future, callback):
        IOLoop.current().add_future(future, callback)

    # Requests handling

    def _execute(self, transforms, *args, **kwargs):
        RequestContext.set('handler_name', repr(self))
        return super(BaseHandler, self)._execute(transforms, *args, **kwargs)

    @tornado.web.asynchronous
    def get(self, *args, **kwargs):
        self.log.stage_tag('prepare')
        self._call_preprocessors(self.preprocessors, partial(self._save_return_value, self.get_page))
        self._finish_page()

    @tornado.web.asynchronous
    def post(self, *args, **kwargs):
        self.log.stage_tag('prepare')
        self._call_preprocessors(self.preprocessors, partial(self._save_return_value, self.post_page))
        self._finish_page()

    @tornado.web.asynchronous
    def head(self, *args, **kwargs):
        self.log.stage_tag('prepare')
        self._call_preprocessors(self.preprocessors, partial(self._save_return_value, self.get_page))
        self._finish_page()

    @tornado.web.asynchronous
    def delete(self, *args, **kwargs):
        self.log.stage_tag('prepare')
        self._call_preprocessors(self.preprocessors, partial(self._save_return_value, self.delete_page))
        self._finish_page()

    @tornado.web.asynchronous
    def put(self, *args, **kwargs):
        self.log.stage_tag('prepare')
        self._call_preprocessors(self.preprocessors, partial(self._save_return_value, self.put_page))
        self._finish_page()

    def options(self, *args, **kwargs):
        raise HTTPError(405, headers={'Allow': ', '.join(self.__get_allowed_methods())})

    def _save_return_value(self, handler_method, *args, **kwargs):
        def is_handler_method(function_name):
            return function_name in {'get_page', 'post_page', 'put_page', 'delete_page'}

        return_value = handler_method(*args, **kwargs)

        if hasattr(self, 'handle_return_value'):
            method_name = handler_method.__name__
            if is_handler_method(method_name) and method_name not in self._returned_methods:
                self._returned_methods.add(method_name)
                self.handle_return_value(method_name, return_value)

    def get_page(self):
        """ This method can be implemented in the subclass """
        raise HTTPError(405, headers={'Allow': ', '.join(self.__get_allowed_methods())})

    def post_page(self):
        """ This method can be implemented in the subclass """
        raise HTTPError(405, headers={'Allow': ', '.join(self.__get_allowed_methods())})

    def put_page(self):
        """ This method can be implemented in the subclass """
        raise HTTPError(405, headers={'Allow': ', '.join(self.__get_allowed_methods())})

    def delete_page(self):
        """ This method can be implemented in the subclass """
        raise HTTPError(405, headers={'Allow': ', '.join(self.__get_allowed_methods())})

    def __get_allowed_methods(self):
        return [name for name in ('get', 'post', 'put', 'delete') if '{0}_page'.format(name) in vars(self.__class__)]

    # HTTP client methods

    def modify_http_client_request(self, request):
        return request

    # Finish page

    def check_finished(self, callback, *args, **kwargs):
        original_callback = callback
        if args or kwargs:
            callback = partial(callback, *args, **kwargs)

        def wrapper(*args, **kwargs):
            if self._finished:
                self.log.warn('page was already finished, {0} ignored'.format(original_callback))
            else:
                callback(*args, **kwargs)

        return wrapper

    def _finish_page(self):
        self.finish_group.try_finish()

    def finish_with_postprocessors(self):
        self.finish_group.finish()

    def _finish_page_cb(self):
        if not self._finished:
            def _callback():
                self.log.stage_tag('page')

                if self.text is not None:
                    producer = self._generic_producer
                elif not self.json.is_empty():
                    producer = self.json_producer
                else:
                    producer = self.xml_producer

                self.log.debug('using %s producer', producer)
                producer(partial(self._call_postprocessors, self._template_postprocessors, self.finish))

            self._call_postprocessors(self._early_postprocessors, _callback)
        else:
            self.log.warning('trying to finish already finished page, probably bug in a workflow, ignoring')

    def on_connection_close(self):
        self.finish_group.abort()
        self.log.stage_tag('page')
        self.log.log_stages(408)
        self.cleanup()

    def register_exception_hook(self, exception_hook):
        """
        Adds a function to the list of hooks, which are executed when `log_exception` is called.
        `exception_hook` must have the same signature as `log_exception`
        """
        self._exception_hooks.append(exception_hook)

    def log_exception(self, typ, value, tb):
        super(BaseHandler, self).log_exception(typ, value, tb)

        for exception_hook in self._exception_hooks:
            exception_hook(typ, value, tb)

    def send_error(self, status_code=500, **kwargs):
        """`send_error` is adapted to support `write_error` that can call
        `finish` asynchronously.
        """

        self.log.stage_tag('page')

        if self._headers_written:
            super(BaseHandler, self).send_error(status_code, **kwargs)

        self.clear()

        reason = None
        if 'exc_info' in kwargs:
            exception = kwargs['exc_info'][1]
            if isinstance(exception, HTTPError) and exception.reason:
                reason = exception.reason

        self.set_status(status_code, reason=reason)

        try:
            self.write_error(status_code, **kwargs)
        except Exception:
            self.log.exception('Uncaught exception in write_error')
            if not self._finished:
                self.finish()

    def write_error(self, status_code=500, **kwargs):
        """`write_error` can call `finish` asynchronously.
        This allows, for example, asynchronous templating on error pages.
        """

        if 'exc_info' in kwargs:
            exception = kwargs['exc_info'][1]
        else:
            exception = None

        headers = getattr(exception, 'headers', None)
        override_content = any(getattr(exception, x, None) is not None for x in ('text', 'xml', 'json'))
        finish_with_exception = isinstance(exception, HTTPError) and override_content

        if headers:
            for (name, value) in iteritems(headers):
                self.set_header(name, value)

        if finish_with_exception:
            self.json.clear()

            if getattr(exception, 'text', None) is not None:
                self.doc.clear()
                self.text = exception.text
            elif getattr(exception, 'json', None) is not None:
                self.text = None
                self.doc.clear()
                self.json.put(exception.json)
            elif getattr(exception, 'xml', None) is not None:
                self.text = None
                # cannot clear self.doc due to backwards compatibility, a bug actually
                self.doc.put(exception.xml)

            self.finish_with_postprocessors()
            return

        self.set_header('Content-Type', 'text/html; charset=UTF-8')
        return super(BaseHandler, self).write_error(status_code, **kwargs)

    def cleanup(self):
        if hasattr(self, 'active_limit'):
            self.active_limit.release()

    def finish(self, chunk=None):
        self.log.stage_tag('postprocess')
        super(BaseHandler, self).finish(chunk)
        self.cleanup()

    def flush(self, include_footers=False, **kwargs):
        self.log.stage_tag('finish')
        self.log.info('finished handler %r', self)

        if self._prepared and self.debug.debug_mode.enabled:
            try:
                self._response_size = sum(map(len, self._write_buffer))
                original_headers = {'Content-Length': str(self._response_size)}
                response_headers = dict(self._headers, **original_headers)

                original_response = {
                    'buffer': base64.b64encode(b''.join(self._write_buffer)),
                    'headers': response_headers,
                    'code': self._status_code
                }

                response_headers_with_cookies = self._generate_headers().splitlines()
                response_headers_with_cookies = filter(None, response_headers_with_cookies)
                res = self.debug.get_debug_page(
                    self._status_code, response_headers_with_cookies,
                    original_response, self.log.get_current_total()
                )

                if self.debug.debug_mode.inherited:
                    self.set_header(PageHandlerDebug.DEBUG_HEADER_NAME, 'true')

                self.set_header('Content-disposition', '')
                self.set_header('Content-Length', str(len(res)))
                self._write_buffer = [res]
                self._status_code = 200

            except Exception:
                self.log.exception('cannot write debug info')

        super(BaseHandler, self).flush(include_footers=False, **kwargs)

    def _log(self):
        super(BaseHandler, self)._log()
        self.log.stage_tag('flush')
        self.log.log_stages(self._status_code)

    # Preprocessors and postprocessors

    def _call_preprocessors(self, preprocessors, callback):
        self._chain_functions(iter(preprocessors), callback, 'preprocessor')

    def _call_postprocessors(self, postprocessors, callback, *args):
        self._chain_functions(iter(postprocessors), callback, 'postprocessor', *args)

    def _chain_functions(self, functions, callback, chain_type, *args):
        try:
            func = next(functions)
            start_time = time.time()

            def _callback(*args):
                time_delta = (time.time() - start_time) * 1000
                self.log.debug('finished %s "%r" in %.2fms', chain_type, func, time_delta)
                self._chain_functions(functions, callback, chain_type, *args)

            func(self, *(args + (_callback,)))
        except StopIteration:
            callback(*args)

    @staticmethod
    def add_preprocessor(*preprocessors_list):
        def _method_wrapper(fn):
            def _method(self, *args, **kwargs):
                callback = partial(self._save_return_value, fn, self, *args, **kwargs)
                return self._call_preprocessors(preprocessors_list, callback)
            return _method
        return _method_wrapper

    def add_template_postprocessor(self, postprocessor):
        self._template_postprocessors.append(postprocessor)

    def add_early_postprocessor(self, postprocessor):
        self._early_postprocessors.append(postprocessor)

    # Producers

    def _generic_producer(self, callback):
        self.log.debug('finishing plaintext')
        callback(self.text)

    def xml_from_file(self, filename):
        return self.xml_producer.xml_from_file(filename)

    def set_xsl(self, filename):
        return self.xml_producer.set_xsl(filename)

    def set_template(self, filename):
        return self.json_producer.set_template(filename)


class PageHandler(BaseHandler):
    def group(self, futures, callback=None, name=None):
        return self._http_client.group(futures, callback, name)

    def get_url(self, url, data=None, headers=None, connect_timeout=None, request_timeout=None, callback=None,
                follow_redirects=True, labels=None, add_to_finish_group=True,
                parse_response=True, parse_on_error=False):

        return self._http_client.get_url(
            url, data=data, headers=headers, connect_timeout=connect_timeout, request_timeout=request_timeout,
            callback=callback, follow_redirects=follow_redirects, labels=labels,
            add_to_finish_group=add_to_finish_group, parse_response=parse_response, parse_on_error=parse_on_error
        )

    def head_url(self, url, data=None, headers=None, connect_timeout=None, request_timeout=None, callback=None,
                 follow_redirects=True, labels=None, add_to_finish_group=True):

        return self._http_client.head_url(
            url, data=data, headers=headers, connect_timeout=connect_timeout, request_timeout=request_timeout,
            callback=callback, follow_redirects=follow_redirects, labels=labels,
            add_to_finish_group=add_to_finish_group
        )

    def post_url(self, url, data='', headers=None, files=None, connect_timeout=None, request_timeout=None,
                 callback=None, follow_redirects=True, content_type=None, labels=None,
                 add_to_finish_group=True, parse_response=True, parse_on_error=False):

        return self._http_client.post_url(
            url, data=data, headers=headers, files=files,
            connect_timeout=connect_timeout, request_timeout=request_timeout,
            callback=callback, follow_redirects=follow_redirects, content_type=content_type, labels=labels,
            add_to_finish_group=add_to_finish_group, parse_response=parse_response, parse_on_error=parse_on_error
        )

    def put_url(self, url, data='', headers=None, connect_timeout=None, request_timeout=None, callback=None,
                content_type=None, labels=None, add_to_finish_group=True, parse_response=True, parse_on_error=False):

        return self._http_client.put_url(
            url, data=data, headers=headers, connect_timeout=connect_timeout, request_timeout=request_timeout,
            callback=callback, content_type=content_type, labels=labels,
            add_to_finish_group=add_to_finish_group, parse_response=parse_response, parse_on_error=parse_on_error
        )

    def delete_url(self, url, data=None, headers=None, connect_timeout=None, request_timeout=None, callback=None,
                   content_type=None, labels=None, add_to_finish_group=True, parse_response=True, parse_on_error=False):

        return self._http_client.delete_url(
            url, data=data, headers=headers, connect_timeout=connect_timeout, request_timeout=request_timeout,
            callback=callback, content_type=content_type, labels=labels,
            add_to_finish_group=add_to_finish_group, parse_response=parse_response, parse_on_error=parse_on_error
        )


class ErrorHandler(tornado.web.ErrorHandler, PageHandler):
    def initialize(self, status_code, logger=None):
        # Hides logger keyword argument from incompatible tornado versions
        super(ErrorHandler, self).initialize(status_code)


class RedirectHandler(tornado.web.RedirectHandler, PageHandler):
    def initialize(self, url, permanent=True, logger=None):
        # Hides logger keyword argument from incompatible tornado versions
        super(RedirectHandler, self).initialize(url, permanent)
