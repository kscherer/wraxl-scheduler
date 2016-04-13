"""Testing pieces of the scheduler"""
import unittest
import uuid
import time

from ..lava_watcher import (LavaQueueWatcher, running_qemu_jobs, get_workers,
                            pending_workers, choose_pending_device_type)
from rq import Queue
from redis import Redis
from docker import Client


class MockRPC1(object):
    def all_jobs(self):
        return [
            [4, 'test-job', 'running', 'real_device', None, {'name': 'qemu-x86_64'}, None],
            [3, 'test-job', 'submitted', None, None, {'name': 'qemu-aarch64'}, None],
            [2, 'test-job', 'submitted', None, None, {'name': 'qemu-x86_64'}, None],
            [1, 'test-job', 'running', 'lava-worker-qemu-x86_64', None, {'name': 'qemu-x86_64'}, None]]

    def all_devices(self):
        return [
            ['yow-kscherer-d2-qemu-aarch64', 'qemu-aarch64', 'offline', None],
            ['yow-kscherer-d2-qemu-x86_64', 'qemu-x86_64', 'offline', None],
            ['yow-ovp-test', 'vlm_pxe', 'offline', None],
            ['ala-blade23-qemu-x86_64', 'qemu-x86_64', 'offline', None],
            ['ala-blade23-qemu-aarch64', 'qemu-aarch64', 'offline', None]]


    def pending_jobs_by_device_type(self):
        return {'qemu-x86_64': 2, 'qemu-aarch64': 1, 'panda': 3}


def enqueue(queue, name, image, cmd, labels={}):
    """Create job dict and enqueue"""
    task_id = str(uuid.uuid4())
    job = {'name': name, 'docker_image': image, 'task_id': task_id,
           'location': 'ala', 'cmd': cmd, 'labels': labels}

    queue.enqueue('wraxl_queue.exec_cmd', job, timeout=100000)


class MockScheduler(object):
    def __init__(self, redis_conn):
        self.redis_conn = redis_conn


TEST_REDIS_PORT = 47876
cli = Client(base_url='unix://var/run/docker.sock')


class TestLava(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.container = cli.create_container(image='redis:2.8', ports=[6379],
                                             host_config=cli.create_host_config(
                                                 port_bindings={6379: TEST_REDIS_PORT}))
        cli.start(container=cls.container.get('Id'))
        time.sleep(1)
        cls.redis_conn = Redis(host='localhost', port=TEST_REDIS_PORT)

    @classmethod
    def tearDownClass(cls):
        cli.kill(container=cls.container.get('Id'))
        cli.remove_container(container=cls.container.get('Id'), v=True, force=True)
        cls.redis_conn = None

    def test_basic(self):
        scheduler = MockScheduler(self.redis_conn)
        lava_queue_watcher = LavaQueueWatcher(scheduler, 'wraxl/scheduler.yaml')
        self.assertIsNotNone(lava_queue_watcher.config['lava'])
        self.assertIsNotNone(lava_queue_watcher.rpc_server)

    def test_running_qemu_jobs(self):
        jobs = running_qemu_jobs(MockRPC1())
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs['1'], 'lava-worker-qemu-x86_64')

    def test_choose_pending_device_type(self):
        oldest_device_type = choose_pending_device_type(MockRPC1())
        self.assertEqual(oldest_device_type, 'qemu-x86_64')

    def test_get_workers(self):
        valid_workers = get_workers(MockRPC1())
        self.assertIn('yow-kscherer-d2', valid_workers)
        self.assertIn('ala-blade23', valid_workers)
        self.assertEqual(len(valid_workers), 2)

    def test_pending_workers(self):
        queue = Queue('wraxl_high', connection=self.redis_conn)
        num_pending_workers = pending_workers(queue)
        self.assertEqual(num_pending_workers, 0)
        enqueue(queue, "task1", 'fedora23_64', "sleep 10", [('type', 'lava')])
        enqueue(queue, "task2", 'fedora23_64', "sleep 10", [('type', 'coverage')])
        enqueue(queue, "task3", 'fedora23_64', "sleep 10", [('type', 'lava')])
        enqueue(queue, "task3", 'fedora23_64', "sleep 10")
        num_pending_workers = pending_workers(queue)
        self.assertEqual(num_pending_workers, 2)
