"""Wrappers for task info and status"""

import uuid

from mesos.interface import mesos_pb2
from .util import (split_escape_quotes, DOCKER_NO_NETWORK, DOCKER_VOLUMES,
                   DOCKER_RUN_PRIVILEGED)


def get_task_id():
    """Create unique task id"""
    return str(uuid.uuid4())


def add_resource(task, name, value):
    """Generic resource add function"""
    if value >= 0.1:
        resource = task.resources.add()
        resource.name = name
        resource.type = mesos_pb2.Value.SCALAR
        resource.scalar.value = value


def add_resources(task, taskspec):
    """Convert task spec into specific add resource calls"""
    add_resource(task, "cpus", taskspec['cpus'])
    add_resource(task, "mem", taskspec['mem'])
    add_resource(task, "high", taskspec['high'])
    add_resource(task, "low", taskspec['low'])
    add_resource(task, "world", taskspec['world'])


class Task():
    """Wrapper class to make protobuf class cleaner to setup"""
    def __init__(self, name, image, offer=None,
                 task_id=None):
        self.taskinfo = mesos_pb2.TaskInfo()
        if task_id:
            self.taskinfo.task_id.value = task_id
        else:
            self.taskinfo.task_id.value = get_task_id()
        self.taskinfo.name = name
        self.taskinfo.container.type = mesos_pb2.ContainerInfo.DOCKER
        self.taskinfo.container.docker.image = image
        self.taskinfo.container.docker.network = mesos_pb2.ContainerInfo.DockerInfo.BRIDGE
        self.taskinfo.container.docker.force_pull_image = False
        if offer:
            self.taskinfo.set_slave(offer)
        self.add_env([('MESOS_TASK_ID', task_id)])
        self.add_volumes(DOCKER_VOLUMES)

    def id(self):
        return self.taskinfo.task_id.value

    def set_slave(self, offer):
        self.taskinfo.slave_id.value = offer.slave_id.value
        self.add_env([('MESOS_AGENT_HOSTNAME', offer.hostname)])

    def add_resources(self, taskspec):
        add_resources(self.taskinfo, taskspec)

    def set_cmd(self, cmd):
        full_cmd = []
        if isinstance(cmd, basestring):
            full_cmd = split_escape_quotes(cmd)
        elif isinstance(cmd, list):
            full_cmd = cmd

        # if the container has a 32bit filesystem, call setarch
        # so that uname -m returns i686 as expected
        if '_32' in self.taskinfo.container.docker.image:
            full_cmd = ['/usr/bin/setarch', 'i386'] + full_cmd

        # Set shell to false to use dumb-init entrypoint in images
        # This requires the command and args to be passed separately
        self.taskinfo.command.shell = False
        self.taskinfo.command.value = full_cmd[0]
        self.taskinfo.command.arguments.extend(full_cmd[1:])

    def set_hostname(self, hostname):
        self.taskinfo.container.hostname = hostname
        self.add_env([('HOSTNAME', hostname)])

    def set_options(self, options):
        if DOCKER_NO_NETWORK in options:
            # when there is no network, force hostname to localhost which is
            # always in /etc/hosts and will prevent DNS lookups when using hostname -s
            self.taskinfo.container.hostname = 'localhost'
            self.taskinfo.container.docker.network = mesos_pb2.ContainerInfo.DockerInfo.NONE
        if DOCKER_RUN_PRIVILEGED in options:
            self.taskinfo.container.docker.privileged = True

    def add_env(self, env):
        for name, value in env:
            env = self.taskinfo.command.environment.variables.add()
            env.name = name
            env.value = value

    def add_volumes(self, volumes):
        for host_path, container_path, mode in volumes:
            volume = self.taskinfo.container.volumes.add()
            volume.host_path = host_path
            volume.container_path = container_path
            volume.mode = mode

    def add_labels(self, labels):
        for key, value in labels:
            label = self.taskinfo.labels.labels.add()
            label.key = key
            label.value = value