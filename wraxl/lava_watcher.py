"""Watches lava server queue and start wraxl jobs when lava test jobs are queued"""

import os
import sys
import time
import xmlrpclib
import logging
import socket
import ssl
from datetime import datetime, timedelta

import yaml
from rq import Queue
from redis import ConnectionError
from mesos.interface import mesos_pb2
import requests

from . import util


logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('lava_watcher')
logging.getLogger("requests").setLevel(logging.WARNING)


def validate_server(rpc_server, user):
    try:
        whoami = rpc_server.system.whoami()
    except (socket.error, xmlrpclib.Fault, xmlrpclib.ProtocolError,
            xmlrpclib.ResponseError):
        log.warning("Unable to authenticate to Lava as %s", user)
        return False

    if user != whoami:
        return False
    return True


class LavaRPC(object):
    def __init__(self):
        self.lava_host = None
        self.user = None
        self.token = None
        self.rpc_server = None
        self.last_connect_attempt = datetime.min

    def create_rpc_server(self, lava_host, user, token):
        if self.rpc_server is None or self.lava_host != lava_host or \
           self.user != user or self.token != token:
            self.user = user
            self.lava_host = lava_host
            self.token = token
            self.last_connect_attempt = datetime.utcnow()
            if hasattr(ssl, '_create_unverified_context'):
                new_rpc_server = xmlrpclib.ServerProxy(
                    "https://%s:%s@%s/RPC2" % (user, token, lava_host),
                    context=ssl._create_unverified_context())
            else:
                new_rpc_server = xmlrpclib.ServerProxy(
                    "https://%s:%s@%s/RPC2" % (user, token, lava_host))

            if validate_server(new_rpc_server, user):
                self.rpc_server = new_rpc_server
            else:
                self.rpc_server = None

    def check_connection(self):
        if self.rpc_server is None and \
           datetime.utcnow() - self.last_connect_attempt > timedelta(seconds=5):
            self.create_rpc_server(self.lava_host, self.user, self.token)

    def all_jobs(self):
        self.check_connection()
        if self.rpc_server is not None:
            return self.rpc_server.scheduler.all_jobs()
        return []

    def all_devices(self):
        self.check_connection()
        if self.rpc_server is not None:
            return self.rpc_server.scheduler.all_devices()
        return []

    def is_valid(self):
        self.check_connection()
        return self.rpc_server is not None


class LavaQueueWatcher(object):
    def __init__(self, scheduler, scheduler_config, lava_server):
        basedir = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.config_file = os.path.join(basedir, scheduler_config)
        self.scheduler = scheduler
        self.shutting_down = False
        self.rpc_server = LavaRPC()
        self.config_mtime = 0
        self.config = {}
        self.redis_conn = scheduler.redis_conn
        self.queue = None
        self.queue_prefix = None
        self.last_worker_query = datetime.min
        self.valid_workers = []
        self.lava_server = lava_server
        self.load_config()

    def load_config(self):
        """Load configuration from yaml configuration file"""
        mtime = util.get_file_mtime(self.config_file)

        # reload the config file if the mtime is newer
        if mtime > self.config_mtime:
            log.info('Loading config file %s', self.config_file)
            self.config_mtime = mtime
            with open(self.config_file) as config_yaml:
                self.config = yaml.load(config_yaml)

            # Set the log level if defined in the config
            if 'log_level' in self.config:
                log.info('Set Log Level to %s', self.config['log_level'])
                log.setLevel(self.config['log_level'])

            self._setup_lava_rpc()

            self.queue_prefix = self.config['queue_prefix']
            self.queue = Queue(self.queue_prefix + '_high',
                               connection=self.redis_conn)
            self.lava_server_ip = socket.gethostbyname(self.config['lava'])

    def _setup_lava_rpc(self):
        self.rpc_server.create_rpc_server(self.lava_server,
                                          self.config.get('lava_user'),
                                          self.config.get('lava_token'))

    def shutdown(self):
        self.shutting_down = True

    def master_address(self):
        return self.scheduler.master_address

    def is_valid_worker(self, worker_hostname):
        return worker_hostname in self._get_valid_workers()

    def _get_valid_workers(self):
        if datetime.utcnow() - self.last_worker_query > timedelta(seconds=60):
            self.last_worker_query = datetime.utcnow()
            self.valid_workers = get_workers(self.rpc_server)

        return self.valid_workers


def check_lava_queue(watcher):
    watcher.load_config()
    num_pending_qemu_jobs, num_running_qemu_jobs = num_qemu_jobs(watcher.rpc_server)
    num_pending_workers = pending_workers(watcher.queue)
    num_running_workers = running_workers(watcher)

    # a running worker may not have transitioned lava state to running yet
    num_started_jobs = num_running_workers - num_running_qemu_jobs

    if num_pending_qemu_jobs - num_pending_workers - num_started_jobs > 0:
        device_type = choose_pending_device_type(watcher.rpc_server)
        launch_qemu_worker(watcher.queue, device_type, watcher.lava_server_ip)


def get_master_json(master_uri, endpoint):
    try:
        request = requests.get(master_uri + '/master/' + endpoint)
    except requests.ConnectionError:
        return []

    if request.status_code == 200:
        try:
            return request.json()[endpoint]
        except ValueError:
            return []


def num_qemu_jobs(rpc_server):
    num_submitted = 0
    num_running = 0
    jobs = rpc_server.all_jobs()
    for job in jobs:
        if job[2] == 'running' and '-qemu-' in job[3]:
            num_running += 1
        elif job[2] == 'submitted' and job[5]['name'].startswith('qemu-'):
            num_submitted += 1

    return (num_submitted, num_running)


def running_qemu_jobs(rpc_server):
    running_jobs = {}
    jobs = rpc_server.all_jobs()
    for job in jobs:
        if job[2] == 'running' and '-qemu-' in job[3]:
            running_jobs[str(job[0])] = job[3]

    return running_jobs


def get_workers(rpc_server):
    valid_workers = []
    devices = rpc_server.all_devices()
    for device in devices:
        device_type = device[1]
        if device_type.startswith('qemu-'):
            # assume device type is appended to worker hostname
            device_hostname = device[0]
            worker_hostname = device_hostname.replace('-' + device_type, '')
            if worker_hostname not in valid_workers:
                valid_workers.append(worker_hostname)

    return valid_workers


def pending_workers(queue):
    total = 0
    try:
        jobs = queue.jobs
    except ConnectionError:
        return 0

    for job in jobs:
        # The Python RQ job is not in the correct state. skip it
        if not job.is_queued:
            continue

        # expects all args for job in a dict in first arg
        args = job.args[0]

        labels = args.get('labels', [])
        for key, value in labels:
            if key == 'type' and value == 'lava':
                total += 1

    return total


def running_workers(watcher):
    total = 0
    master_uri = watcher.master_address()
    if master_uri is not None:
        master_uri = 'http://' + master_uri
        tasks = get_master_json(master_uri, 'tasks')

        for task in tasks:
            if task['state'] == 'TASK_RUNNING' and 'lava-worker' in task['name']:
                total += 1

    return total


def choose_pending_device_type(rpc_server):
    oldest_submitted_job = sys.maxsize
    oldest_device_type = ""
    jobs = rpc_server.all_jobs()
    for job in jobs:
        job_device_type = job[5]['name']
        if job[2] == 'submitted' and job_device_type.startswith('qemu-'):
            job_id = job[0]
            if job_id < oldest_submitted_job:
                oldest_submitted_job = job_id
                oldest_device_type = job_device_type

    return oldest_device_type


def launch_qemu_worker(queue, device_type, lava_server_ip):
    job = {'name': 'lava-worker-' + device_type, 'docker_image': 'lava-worker:2016.3',
           'options': [util.DOCKER_RUN_PRIVILEGED], 'cmd': '/bin/lava_worker_start.sh'}

    job['environment'] = [('LAVA_SERVER_IP', lava_server_ip),
                          ('LAVA_DEVICE_TYPE', device_type),
                          ('LAVA_WORKER_IDLE_CHECK', 'yes')]
    job['volumes'] = [("/mnt/docker", "/tmp", mesos_pb2.Volume.RW)]
    job['labels'] = [('type', 'lava'), ('device_type', device_type)]

    try:
        log.info("Enqueue lava-worker for device type %s.", device_type)
        queue.enqueue('wraxl_queue.exec_cmd', job, timeout=10800)
    except ConnectionError:
        log.warning("Unable to connect to Redis server")


def run_lava_watcher_async(watcher):
    """Watch lava server queue"""
    while watcher.shutting_down is False:
        if watcher.rpc_server.is_valid():
            check_lava_queue(watcher)
        time.sleep(5)
