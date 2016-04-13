"""Functions to retrieve current leading mesos master"""

import os
import re
import json
import kazoo.client
import kazoo.exceptions
import kazoo.handlers.threading
import requests

from .memoize_with_expiry import memoize_with_expiry


@memoize_with_expiry(expiry_time=5, num_args=1)
def get_zk_leading_master(masters):
    """Takes a list of zookeeper hosts and returns the leading mesos
    master.  The zookeeper election algorithm uses a special type of
    entry that is sequential and ephemeral. Ephemeral means that when
    the connection from a master to zookeeper drops, the entry is
    deleted. Sequential means each entry gets a counter appended to it
    and the leader is the node with the lowest counter number.  This
    code was inspired by mesos-cli, but works only for mesos versions
    0.24.0+
    """
    # assume format zk://<hosts>/mesos
    hosts, path = masters[5:].split("/", 1)
    path = "/" + path

    zk = kazoo.client.KazooClient(hosts=hosts)
    zk.start(timeout=1)

    def master_id(key):
        return int(key.split("_")[-1])

    def get_masters():
        return [x for x in zk.get_children(path)
                if re.search(r"\d+", x)]

    # each entry looks like json.info_XXX where XXX is the counter
    masters = get_masters()
    leader = sorted(masters, key=lambda x: master_id(x))
    data, _ = zk.get(os.path.join(path, leader[0]))

    # mesos 0.24.0+ stores master info in json
    try:
        parsed = json.loads(data)
        if parsed and "address" in parsed:
            ip = parsed["address"].get("ip")
            port = parsed["address"].get("port")
    except ValueError:
        ip = None

    zk.stop()
    zk.close()
    if ip and port:
        return "{ip}:{port}".format(ip=ip, port=port)
    return ""


@memoize_with_expiry(expiry_time=5, num_args=2)
def get_leading_master_redirect(mesos_master, port):
    """Given the ip of a mesos master, check the redirect. Note that there
    is the possibility that the redirect will not actually be the
    master, so it is recommended to use the zookeeper lookup instead.
    """
    try:
        response = requests.head('http://' + mesos_master + ':' + str(port) +
                                 '/master/redirect', timeout=5, allow_redirects=False)
        location = response.headers['Location']
        return 'http:' + location
    except requests.exceptions.RequestException:
        return None
