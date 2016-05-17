#!/usr/bin/env python

# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import sys
import random
import signal
import logging
import time
import platform
from threading import Thread
from datetime import datetime, timedelta

import yaml
from rq import Queue
from redis import Redis, ConnectionError
from mesos.interface import Scheduler
from mesos.interface import mesos_pb2
from mesos.native import MesosSchedulerDriver

from .util import (id_generator, split_escape_quotes, drop_privileges,
                   get_file_mtime, DOCKER_NO_NETWORK)
from .web_interface import WraxlHttpServer, run_httpserver
from .lava_watcher import LavaQueueWatcher, run_lava_watcher_async
from .task import Task, get_task_id

VERSION = '0.2.0'

HIGH_TASKSPEC = {'cpus': 4.0, 'mem': 15360.0, 'high': 1.0, 'low': 0.0, 'world': 0.0}
# Low priority jobs get lower cgroup priority but will use available cpu
LOW_TASKSPEC = {'cpus': 1.0, 'mem': 15360.0, 'high': 0.0, 'low': 1.0, 'world': 0.0}
WORLD_TASKSPEC = {'cpus': 1.0, 'mem': 30720.0, 'high': 0.0, 'low': 0.0, 'world': 1.0}

THREE_HOURS = 60 * 60 * 3

ERROR_STATES = (
    mesos_pb2.TASK_FAILED,
    mesos_pb2.TASK_KILLED,
    mesos_pb2.TASK_LOST,
    mesos_pb2.TASK_ERROR
)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('wraxl_scheduler')


class WraxlScheduler(Scheduler):
    """
    If the offer provides enough resources,
    choose a configuration at random and build it
    """
    def __init__(self, scheduler_config, config_dir, redis):
        # by default look in same path as scheduler. If absolute path use that
        basedir = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.config_file = os.path.join(basedir, scheduler_config)
        self.config_dir = config_dir

        if not os.path.exists(self.config_file):
            sys.exit('ERROR: Config file %s was not found!' % self.config_file)

        # force the config file to be loaded the first time
        self.config_mtime = 0
        self.config = {}
        self.redis = redis
        self.redis_conn = None
        self.load_config()
        self.shutting_down = False
        self.tasks = {}
        self.agent_failure = {}
        self.agent_backoff = {}
        self.master_address = None
        self.driver = None
        self.lava_watcher = None

    def load_config(self):
        """Load configuration from yaml configuration file"""
        mtime = get_file_mtime(self.config_file)

        # reload the config file if the mtime is newer
        if mtime > self.config_mtime:
            log.info('Loading config file %s', self.config_file)
            self.config_mtime = mtime
            with open(self.config_file) as config_yaml:
                self.config = yaml.load(config_yaml)

            if 'config_dir' in self.config:
                self.config_dir = self.config['config_dir']

            log.info('Will search %s for randconfigs', self.config_dir)

            # Set the log level if defined in the config
            if 'log_level' in self.config:
                log.info('Set Log Level to %s', self.config['log_level'])
                log.setLevel(self.config['log_level'])

            # Tell RQ what Redis connection to use, default to lpd-web
            if self.redis is None and 'redis' in self.config:
                self.redis = self.config['redis']

            log.info('Set Redis Server to %s', self.redis)
            self.redis_conn = Redis(self.redis)

            if 'registry' in self.config:
                self.registry = self.config['registry']

            for release in self.config['releases']:
                release['configs_mtime'] = 0
                self.load_randconfig(release)
                release['worldconfigs_mtime'] = 0
                self.load_worldconfig(release)

    def load_randconfig(self, release):
        """Load the randconfigs from the configs file if newer"""
        randconfig_file = os.path.join(self.config_dir, release['configs'])
        mtime = get_file_mtime(randconfig_file)

        # reload the config file if the mtime is newer
        if mtime > release['configs_mtime']:
            log.info('Loading randconfigs file for %s', release['branch'])
            release['configs_mtime'] = mtime
            release['randconfigs'] = open(randconfig_file).read().splitlines()

    def load_worldconfig(self, release):
        """Load the worldconfigs from the configs file if newer"""
        worldconfig_file = os.path.join(self.config_dir, release['world_configs'])
        mtime = get_file_mtime(worldconfig_file)

        # reload the config file if the mtime is newer
        if mtime > release['worldconfigs_mtime']:
            log.info('Loading worldconfigs file for %s', release['branch'])
            release['worldconfigs_mtime'] = mtime
            release['worldrandconfigs'] = open(worldconfig_file).read().splitlines()

    def select_release(self):
        """Select a release using weighted random """
        totals = []
        running_total = 0

        for release in self.config['releases']:
            running_total += float(release['weight'])
            totals.append(running_total)

        rnd = random.random() * running_total
        for i, total in enumerate(totals):
            if rnd < total:
                return self.config['releases'][i]

    def set_master_address(self, masterInfo):
        self.master_address = "{ip}:{port}".format(
            ip=masterInfo.address.ip, port=masterInfo.address.port)

    def registered(self, driver, frameworkId, masterInfo):
        self.set_master_address(masterInfo)
        self.driver = driver
        log.info("Registered with framework ID %s to master %s",
                 frameworkId.value, self.master_address)
        driver.reconcileTasks([])

    def reregistered(self, driver, masterInfo):
        self.set_master_address(masterInfo)
        self.driver = driver
        log.info("Reregistered to master %s", self.master_address)
        driver.reconcileTasks([])

    def get_rq_task(self, offer, priority="low", location=None):
        """Find first queued job with an image and move to temp queue"""

        # If Redis is down, the scheduler cannot find queued tasks
        try:
            queues = Queue.all(connection=self.redis_conn)
        except ConnectionError:
            return None

        # Look for any queued jobs that did not start properly
        temp_queue_prefix = self.config['queue_prefix'] + '_' + priority + '_'
        for queue in queues:
            if queue.name.startswith(temp_queue_prefix):
                jobs = queue.jobs
                for job in jobs:
                    # Only attempt to re-run the job every 3 minutes
                    if datetime.utcnow() - job.meta['last_run_attempt'] > timedelta(seconds=180):
                        # expects all args for job in a dict in first arg
                        args = job.args[0]

                        # Skip jobs not offering in requested location
                        if 'location' in args and args['location'] != location:
                            continue

                        log.warning("Found stale job. Requeuing " + queue.name)
                        return self.create_rq_task(job.meta['image'], queue.name, job, offer)

        queue = Queue('_'.join([self.config['queue_prefix'], priority]),
                      connection=self.redis_conn)
        jobs = queue.jobs
        for job in jobs:
            # The Python RQ job is not in the correct state. skip it
            if not job.is_queued:
                continue

            # expects all args for job in a dict in first arg
            args = job.args[0]

            # If the job specifies a location and the offer is not in that location, skip it
            if 'location' in args and args['location'] != location:
                log.debug('Skipping offer from %s because job requested %s.',
                          location, args['location'])
                continue

            # default to Ubuntu if docker image is not defined
            image = "ubuntu1404_64"
            if 'docker_image' in args:
                image = args['docker_image']

            image_no_tag = image.split(':')[0]

            if image_no_tag == 'lava-worker' and \
               not self.lava_watcher.is_valid_worker(offer.hostname):
                log.debug('Skipping offer from %s because it is not a valid lava worker.',
                          offer.hostname)
                continue

            temp_queue_name = '_'.join([self.config['queue_prefix'],
                                        priority, image_no_tag,
                                        id_generator()])
            temp_queue = Queue(temp_queue_name, connection=self.redis_conn,
                               default_timeout=THREE_HOURS)
            queue.remove(job)
            temp_queue.enqueue_job(job)

            return self.create_rq_task(image, temp_queue_name, job, offer)

        return None

    def resourceOffers(self, driver, offers):
        """Process offers from mesos slaves"""
        log.debug("Got %d resource offers", len(offers))

        # make sure the latest configurations are being used
        self.load_config()

        for offer in offers:

            if self.shutting_down:
                log.info("Shutting down: declining offer on %s", offer.hostname)
                driver.declineOffer(offer.id)
                continue

            if self.is_agent_failing(offer.slave_id.value):
                log.info("Agent failing: declining offer on %s", offer.hostname)
                driver.declineOffer(offer.id)
                continue

            tasks = []
            offer_res = {'cpus': 0.0, 'mem': 0.0, 'high': 0.0, 'low': 0.0, 'world': 0.0}
            for resource in offer.resources:
                if resource.type == mesos_pb2.Value.SCALAR:
                    if resource.name == "cpus":
                        offer_res['cpus'] = resource.scalar.value
                    elif resource.name == "mem":
                        offer_res['mem'] = resource.scalar.value
                    elif resource.name == "high":
                        offer_res['high'] = resource.scalar.value
                    elif resource.name == "low":
                        offer_res['low'] = resource.scalar.value
                    elif resource.name == "world":
                        offer_res['world'] = resource.scalar.value

            location = None
            for attribute in offer.attributes:
                if attribute.type == mesos_pb2.Value.TEXT:
                    if attribute.name == "location":
                        location = attribute.text.value

            log.debug("Got resource offer on %s with %s cpus and %s mem and %s High slots and %s Low slots and %s World slots in location %s",
                      offer.hostname, offer_res['cpus'], offer_res['mem'],
                      offer_res['high'], offer_res['low'], offer_res['world'],
                      location)

            if check_offer(offer_res, HIGH_TASKSPEC):
                # look for a non empty RQ queue
                task = self.get_rq_task(offer, "high", location)
                if task is not None:
                    task.add_resources(HIGH_TASKSPEC)
                    log.info("Accepting offer on %s with %s to start RQ worker on %s",
                             offer.hostname, task.id(), 'high')
                    tasks.append(task.taskinfo)

            if check_offer(offer_res, LOW_TASKSPEC):
                task = self.get_rq_task(offer, "low", location)
                if task is not None:
                    task.add_resources(LOW_TASKSPEC)
                    log.info("Accepting offer on %s with %s to start RQ worker on %s",
                             offer.hostname, task.id(), 'low')
                    tasks.append(task.taskinfo)
                else:
                    release = self.select_release()
                    # reload randconfigs if file has changed
                    self.load_randconfig(release)

                    # select the build configuration and the host randomly
                    args = random.choice(release['randconfigs'])
                    build_name = args.split()[0] + str(release['name'])
                    image = select_host(release, build_name)

                    cmd = create_cmd(image, release['branch'], release['email'])
                    named_args = "--name=" + args.strip()
                    cmd += split_escape_quotes(named_args)

                    task = self.create_task(name=build_name, offer=offer,
                                            image=image)
                    task.add_resources(LOW_TASKSPEC)
                    task.set_cmd(cmd)
                    task.set_options([DOCKER_NO_NETWORK])
                    task.add_labels([('type', 'coverage')])
                    log.info("Accepting Low Priority offer on %s to start task %s",
                             offer.hostname, task.id())
                    tasks.append(task.taskinfo)

            if check_offer(offer_res, WORLD_TASKSPEC):
                # Tasks queued on the world queue get priority over default world builds
                task = self.get_rq_task(offer, "world", location)
                if task is not None:
                    task.add_resources(WORLD_TASKSPEC)
                    log.info("Accepting offer on %s with %s to start RQ worker on %s",
                             offer.hostname, task.id(), 'world')
                    tasks.append(task.taskinfo)
                else:
                    release = self.select_release()
                    # reload world configs if file has changed
                    self.load_worldconfig(release)

                    # select the build configuration and the host randomly
                    args = random.choice(release['worldrandconfigs'])
                    build_name = args.split()[0] + str(release['name'])
                    image = random.choice(release['hosts'])

                    cmd = create_cmd(image, release['branch'], release['email'])
                    cmd += ["--world_build=yes"]
                    named_args = "--name=" + args.strip()
                    cmd += split_escape_quotes(named_args)

                    task = self.create_task(name=build_name, offer=offer,
                                            image=image)
                    task.add_resources(WORLD_TASKSPEC)
                    task.set_cmd(cmd)
                    task.set_options([DOCKER_NO_NETWORK])
                    task.add_labels([('type', 'coverage')])
                    log.info("Accepting World offer on %s to start task %s",
                             offer.hostname, task.id())
                    tasks.append(task.taskinfo)

            # Must call launchTasks with empty task list to reject offers
            driver.launchTasks(offer.id, tasks)

    def statusUpdate(self, driver, update):
        task_id = update.task_id.value
        state = update.state
        agent_id = update.slave_id.value
        message = update.message

        if task_id not in self.tasks:
            self.tasks[task_id] = {}
        task = self.tasks[task_id]
        task['state'] = state
        task['agent'] = agent_id
        log.info("Task %s on agent %s is in state %s",
                 task_id, agent_id, mesos_pb2.TaskState.Name(state))

        if state == mesos_pb2.TASK_FINISHED:
            self.agent_failure[agent_id] = None
            self.agent_backoff[agent_id] = 0

            # consider any task that "finished" in under 30 seconds a failed task
            running_start = task.get('start', datetime.min)

            if datetime.utcnow() - running_start < timedelta(seconds=30):
                log.info("Failing short task run on %s", agent_id)
                state = mesos_pb2.TASK_FAILED
                message = "Task finished with less than minimum build time of 30 seconds"

        if state in ERROR_STATES:
            # start backoff immediately after a failure
            self.agent_failure[agent_id] = datetime.utcnow()
            current_backoff = self.agent_backoff.get(agent_id, 0)
            if current_backoff < 8:
                self.agent_backoff[agent_id] = 8
            elif current_backoff < 1024:
                self.agent_backoff[agent_id] = current_backoff * 2

            log.warning("Task %s is in unexpected state %s with message '%s'",
                        task_id, mesos_pb2.TaskState.Name(state), message)

        # Need to track how long between the running start time and the finished message
        if state == mesos_pb2.TASK_RUNNING and 'start' not in task:
            task['start'] = datetime.utcnow()

    def frameworkMessage(self, driver, executorId, slaveId, message):
        log.info("Received message: %s", repr(str(message)))

    def create_rq_task(self, image, queue, job, offer):
        """Make a dict of all the params required to create RQ task"""
        args = job.args[0]
        job.meta['image'] = image
        job.meta['last_run_attempt'] = datetime.utcnow()
        job.save()

        task = self.create_task(name="RQ " + queue, image=image, offer=offer,
                                task_id=args.get('task_id', get_task_id()))

        cmd = ['/home/wrlbuild/wr-buildscripts/run_rqworker.sh', queue]
        task.set_cmd(cmd)
        task.set_options(args.get('options', []))
        task.add_env(args.get('environment', []))
        task.add_volumes(args.get('volumes', []))
        task.add_parameters(args.get('parameters', []))
        task.add_labels(args.get('labels', []))
        task.add_port_mappings(args.get('port_mappings', []))
        task.set_hostname(args.get('hostname', offer.hostname))

        return task

    def create_task(self, name, image, task_id=None, offer=None):
        if task_id is None:
            task_id = get_task_id()
        task = Task(name=name, image=self.registry + image, task_id=task_id)
        if offer:
            task.set_slave(offer)
        task.add_env([('REDIS_SERVER', self.redis)])
        return task

    def is_agent_failing(self, agent_id):
        """Check if the host has failed recently"""
        last_failure = self.agent_failure.get(agent_id)
        if last_failure is None:
            return False

        backoff = self.agent_backoff.get(agent_id, 0)
        if backoff < 8:
            return False

        # if the last failure is within the backoff time, tell scheduler
        # not to use this agent
        if datetime.utcnow() - last_failure < timedelta(seconds=backoff):
            return True

        return False

    def killTask(self, task_uuid):
        task_id = mesos_pb2.TaskID()
        task_id.value = task_uuid
        self.driver.killTask(task_id)

    def set_lava_watcher(self, lava_watcher):
        self.lava_watcher = lava_watcher


def create_cmd(docker_image, branch, email):
    """Create base command for builds inside containers"""
    cmd = ["/home/wrlbuild/wr-buildscripts/wrlinux_build.sh", "--branch=" + branch]
    cmd += ["--host=" + docker_image]
    # pass email addresses as comma seperated list to avoid space problems
    cmd += ["--email=" + ','.join(email)]
    return cmd


def check_offer(offer, taskspec):
    """Check if the offer contains resources equal or greater than the taskspec"""
    if offer['cpus'] >= taskspec['cpus'] and offer['mem'] >= taskspec['mem'] and \
       offer['high'] >= taskspec['high'] and offer['low'] >= taskspec['low'] and \
       offer['world'] >= taskspec['world']:
        return True

    return False


def select_host(release, config):
    """OVP does not support self-hosted i686 container"""
    if 'ovp' in config:
        filtered_host_list = list(release['hosts'])
        if 'wrl7_32' in filtered_host_list:
            filtered_host_list.remove('wrl7_32')

        return random.choice(filtered_host_list)
    return random.choice(release['hosts'])


def start_scheduler(opts):
    """Start Scheduler"""

    framework = mesos_pb2.FrameworkInfo()

    # framework runs as wrlbuild, but executor must run as root because it calls docker
    framework.user = "root"
    framework.name = "Wraxl Scheduler"
    framework.hostname = opts.hostname

    log.info("Enabling checkpoint for the framework")
    framework.checkpoint = True

    # for failover or restarts, the framework must register
    # with same id
    framework.id.value = "RandomBuilder"
    framework.failover_timeout = 900.0

    scheduler = WraxlScheduler(opts.config, opts.config_dir, opts.redis)

    implicitAcknowledgements = True
    log.info("Searching for mesos master: %s", opts.master)
    driver = MesosSchedulerDriver(scheduler, framework, opts.master,
                                  implicitAcknowledgements)

    # need dict to allow inner function to assign to outer scope variable
    status = {'code': 0}

    def run_driver_async():
        """driver.run() blocks; we run it in a separate thread"""
        status['code'] = 0 if driver.run() == mesos_pb2.DRIVER_STOPPED else 1
        # Ensure that the driver process terminates and expect failover
        driver.stop(True)

    framework_thread = Thread(name='scheduler', target=run_driver_async, args=())
    framework_thread.daemon = True

    # Run flask in separate process so it can be terminated when mesos driver exits
    wraxl_httpserver = WraxlHttpServer(scheduler)
    httpserver_thread = Thread(name='http', target=run_httpserver,
                               args=(wraxl_httpserver,))
    httpserver_thread.daemon = True

    # Start Lava Queue Watcher
    lava_queue_watcher = LavaQueueWatcher(scheduler, opts)
    scheduler.set_lava_watcher(lava_queue_watcher)
    lava_watcher_thread = Thread(name='lava-watcher', target=run_lava_watcher_async,
                                 args=(lava_queue_watcher,))
    lava_watcher_thread.daemon = True

    def graceful_shutdown(signal, frame):
        """Clean shutdown"""
        scheduler.shutting_down = True
        lava_queue_watcher.shutdown()
        # Give any current offer processing time to finish
        time.sleep(1)
        # Graceful exit to http server request processing loop
        wraxl_httpserver.shutdown()
        # Ensure that the driver process terminates and expect failover
        driver.stop(True)

    log.info("(Listening for Ctrl-C)")
    signal.signal(signal.SIGINT, graceful_shutdown)
    signal.signal(signal.SIGTERM, graceful_shutdown)

    framework_thread.start()
    httpserver_thread.start()
    lava_watcher_thread.start()

    while framework_thread.is_alive():
        time.sleep(1)

    # wait for all threads to exit cleanly
    httpserver_thread.join()
    log.info("Http server thread has exited")

    lava_watcher_thread.join()
    log.info("Lava watcher thread has exited")

    framework_thread.join()
    log.info("Framework run loop has stopped with status %s", status['code'])

    sys.exit(status['code'])


def parse_args():
    """Parse command line args"""
    from argparse import ArgumentParser

    descr = '''The WRAXL coverage build scheduler. A mesos framework
    that accepts arbitrary jobs from Python RQ or schedules coverage builds
    if no queued tasks are available.
    '''

    op = ArgumentParser(description=descr)
    op.add_argument('--master', dest='master',
                    default=os.environ.get('MESOS_MASTER', 'localhost:5050'),
                    help='IP address and port (default 5050) of mesos master '
                    'or zookeeper ip address(es) used to find current mesos master.'
                    'Defaults to environment variable MESOS_MASTER if defined.')

    op.add_argument('--config', dest='config',
                    default=os.environ.get('WRAXL_CONFIG', '/etc/wraxl_scheduler.yaml'),
                    help='Path to yaml config file used to define valid releases and hosts.')

    op.add_argument('--hostname', dest='hostname',
                    default=platform.node(),
                    help='The scheduler hostname/ip must be accessible by master.'
                    'If the hostname resolves to 127.0.0.1 this can cause problems.')

    op.add_argument('--redis', dest='redis',
                    default=os.environ.get('WRAXL_REDIS', 'ala-lpgweb2.wrs.com'),
                    help='The name or ip of the redis server used for Python RQ.'
                    'This must be accessible by RQ worker docker containers.'
                    'Defaults to environment variable WRAXL_REDIS if defined.')

    op.add_argument('--config_dir', dest='config_dir',
                    default=os.environ.get('WRAXL_CONFIG_DIR', '/mnt'),
                    help='The directory that holds the randconfig and worldconfig'
                    'As referenced in the main wraxl config file.')

    op.add_argument('--lava_server', dest='lava_server',
                    default=os.environ.get('WRAXL_LAVA_SERVER', 'ala-lpd-lava'),
                    help='The host and port of the lava server')

    opts = op.parse_args()

    if not opts.master:
        op.error('You must provide ip address of mesos master')

    return opts


def main():
    """Main entry"""
    drop_privileges('wrlbuild', 'wrlbuild')

    opts = parse_args()
    start_scheduler(opts)

if __name__ == "__main__":
    main()
