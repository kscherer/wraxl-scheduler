"""Provides web and REST interface to scheduler"""
import logging

from wsgiref.simple_server import make_server
from pyramid.config import Configurator
from pyramid.response import Response

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('wraxl_web')


def root(request):
    return Response('<h1>Task list here</h1>')


class WraxlHttpServer:
    """Class to hold the web server"""
    def __init__(self, scheduler):
        self.scheduler = scheduler
        config = Configurator()
        config.add_route('root', '/')
        config.add_view(root, route_name='root')
        app = config.make_wsgi_app()
        self.server = make_server('0.0.0.0', 8080, app)

    def serve_forever(self):
        """Enter http request processing loop"""
        self.server.serve_forever()

    def shutdown(self):
        """Causes the server_forever loop to exit"""
        self.server.shutdown()


def run_httpserver(wraxl_httpserver):
    """Run the http server on a different thread"""
    try:
        wraxl_httpserver.serve_forever()
    except Exception as exc:
        log.warning("Web Interface exception caught!", exc_info=True)
