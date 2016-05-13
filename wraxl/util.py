"""Util functions for wraxl scheduler"""

import os
import string
import re
import random
import pwd
import grp
import logging

from mesos.interface import mesos_pb2


# Low and world priority tasks have network disabled
DOCKER_NO_NETWORK = '--net=none'
DOCKER_RUN_PRIVILEGED = '--privileged'

# Default volumes for all builds done in docker containers
DOCKER_VOLUMES = [("/home/wrlbuild", "/home/wrlbuild", mesos_pb2.Volume.RW),
                  ("/etc/localtime", "/etc/localtime", mesos_pb2.Volume.RO),
                  ("/sys/fs/cgroup", "/sys/fs/cgroup", mesos_pb2.Volume.RO)]


def id_generator(size=6, chars=string.ascii_uppercase + string.digits):
    """Generate random sequence of characters"""
    return ''.join(random.choice(chars) for _ in range(size))


# When splitting the configurations in randconfigs files into python list
# any args with quotes were not split properly. This reqexp will match
# non-whitespace, but will match spaces inside quotes
REGEXP_SPLIT_ESCAPE_QUOTES = re.compile(r'''((?:[^ "']|"[^"]*"|'[^']*')+)''')


logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('wraxl_scheduler')


def split_escape_quotes(str_with_quotes):
    """
    When using split with a regexp, the portions between the matching
    parts are returned. The splice returns every other match which is
    the portions between the spaces
    """
    return REGEXP_SPLIT_ESCAPE_QUOTES.split(str_with_quotes)[1::2]


def get_file_mtime(fullpath):
    """Return the mtime of a file. If it does not exist return 0"""
    if os.path.exists(fullpath):
        return os.stat(fullpath).st_mtime

    log.warning("Unable to check mtime of file %s.", fullpath)
    return 0


def drop_privileges(uid_name='nobody', gid_name='nobody'):
    """Don't run as root"""
    if os.getuid() != 0:
        # We're not root so, like, whatever dude
        return

    # Get the uid/gid from the name
    running_uid = pwd.getpwnam(uid_name).pw_uid
    running_gid = grp.getgrnam(gid_name).gr_gid

    # Remove group privileges
    os.setgroups([])

    # Try setting the new uid/gid
    os.setgid(running_gid)
    os.setuid(running_uid)

    # Ensure a very conservative umask
    os.umask(022)
