# Local wraxl test setup using Docker Compose

## Introduction

The production version of wraxl is a large distributed system
involving over 50 servers currently, but there needs to be an easy way
to replicate as closely as possible a local version of the system in
order to be able to develop and experiment. This document covers the
setup and running of the local wraxl setup using docker compose.

## Initial setup

The document assumes docker is setup locally using the setup described
in the Readme. It also assumes that the hostname wr-docker-registry is
in /etc/hosts and has the ip of the local docker registry.

Install docker compose 1.5+. Follow the official instructions at
https://docs.docker.com/compose/install/

The official docs say to install the binary into /usr/local/bin, but
anywhere in $PATH will work.

The docker containers currently assume that the local path
/home/wrlbuild exists and that it has uid 1000. Both of these
requirements can be removed and made configuration options, but
currently they are required.

    sudo useradd -b /home -u 1000 -g 1000 wrlbuild

If this fails with the error:

    useradd: UID 1000 is not unique

you may need to stop NIS as follows:

    sudo service ypbind stop
        ypbind stop/waiting
    sudo useradd -b /buildarea -u 1000 -g 1000 -G docker wrlbuild
    sudo service ypbind start

Since the builds require a lot of disk space, if /home/wrlbuild is not
on a large disk, make the home directory on a large disk and make a
symlink.

    sudo useradd -b /buildarea -u 1000 -g 1000 -G docker wrlbuild
    sudo ln -s /buildarea/wrlbuild /home/wrlbuild

Login as the wrlbuild user

    git clone ssh://ala-git/git/lpd-ops/wr-buildscripts.git

The wrlinux_build.sh script also assume that wrlinux-x developer trees
are available in /home/wrlbuild. Each tree should have the branch as
part of the directory name, i.e. `wrlinux-WRLINUX_7_0_HEAD` for WRL7.

## Using docker compose

After the initial setup, everything should be ready for
docker-compose.

    cd wr-buildscripts
    sudo rm -rf /tmp/mesos/*
    ./start_test_wraxl_cluster.sh --registry <registry> --file wraxl_scheduler.yaml

This will download and start five docker containers and link them:
zookeeper, mesos master, mesos slave, the scheduler and redis. To stop the
containers, just press Ctrl-C.

The web UI for the mesos master is available at:

    http://localhost:5050

After the containers have stopped, the mesos log directory needs to be
cleaned manually. It is best to run this before running docker-compose.

    sudo rm -rf /tmp/mesos/*

Docker compose will also automatically reuse exited containers. This
may be useful if you want to restart a task on the queue, but most
times starting the test cluster without existing state is better. Run
the following to clear any exited containers.

    docker rm $(docker ps --filter status=exited -q)

## Running the Scheduler

The current random coverage scheduler has several design limitations:

1) It assumes it is the only mesos scheduler

2) It assumes the slaves have custom mesos high, low and world
resources.

3) It watches three queues that match the resources. The names of
those queues is the queue_prefix as defined in the configuration
(default wraxl) plus the resource. By default the queues are named
`wraxl_high`, `wraxl_low`, `wraxl_world`.

4) By default the docker-compose configuration starts the slave with
one high priority resource and the scheduler only starts jobs on low
or world priority resources. This means that nothing happens when the
scheduler is started.

The scheduler started by docker-compose has a default queue_prefix of
`wraxl`, so the queues are `wraxl_high`, `wraxl_low` and
`wraxl_world`.

To test changes to the scheduler run:

    ./start_test_wraxl_cluster.sh --registry <registry> \
        --file wraxl_scheduler.yaml --file wraxl_sched_test.yml

which will link the local versions into the mesos_scheduler
container. Docker compose 1.5+ supports multiple yml files to override
parameters in previous yml files. The wraxl_sched_test.yml file adds
volume mounts to use a local version of the scheduler for
testing. More files could be added to add/change agent attributes or
other configuration items.

## Scheduler python dependencies

The scheduler depends on several python libraries: redis, rq,
protobuf, mesos.interface and mesos.native. The mesos.native library
includes a compiled extension and is platform dependent. Mesosphere
has made this library available here:

    http://open.mesosphere.com/downloads/mesos/

That python egg is installed using easy_install. Unfortunately the
python egg conflicts with the standard pip flat installation format,
so the mesos.interface library must be installed with:

    pip install --egg mesos.interface

These notes are integrated into the mesos-scheduler docker image. The
Dockerfile for that image is here:

    http://ala-git.wrs.com/cgi-bin/cgit.cgi/lpd-ops/docker-images.git/tree/mesos/Dockerfile-mesos-scheduler

Note that with Mesos 0.24, a new HTTP API will be released which
promises to make the mesos.native and mesos.interface packages
obsolete.

## Running docker jobs

Note that the `test_scheduler_config` does not pull docker images
by default. If the docker image is not present on the local machine
(check with `docker images`) then the job will fail. Check the
/tmp/mesos/slave1/logs for more debugging information.

Now that the scheduler is running, jobs can be enqueued and run.

Here is an example minimal python script to enqueue a custom job:

    # Tell RQ to use local test Redis database
    redis_conn = Redis('localhost')

    # Use wraxl_test_high queue. This must match the queue_prefix
    queue = Queue('wraxl_high', connection=redis_conn)

    # create a job
    job = {'name': "my test job",
           'docker_image': 'ubuntu1404_64',
           # optional: production wraxl supports yow, ala, pek
           #'location': 'yow',
           # optional: add env variables
           #'environment': ['ENV1', 'foo'],
           # optional: add volume mount
           #'volumes': ['/usr/bin/docker', '/usr/bin/docker', 2],
           # command must be an array, script must be in /home/wrlbuild
           'cmd': ['/home/wrlbuild/wr-buildscripts/myscript.sh',
                   '--arg1=arg1', '--arg2=arg2']}

    # timeout is in seconds, this is three hours
    queue.enqueue('wraxl_queue.exec_cmd', job, timeout=10800)

Also see the example script `test/test_enqueue.py` and
`ovp_lava_enqueue.py` for more examples.

## Working with the devbuild components

The `wrgit devbuild` feature has two more pieces that can be started
in this environment: `devbuild_queue_watcher.py` and
`devbuild_watcher.py`. The `devbuild_queue_watcher.py` daemon waits
for devbuild jobs to be placed on a Redis queue and then creates the
requested builds and queues them on wraxl. The `devbuild_watcher.py`
is a python flask web application that display devbuilds that are
currently running.

To start the `devbuild_queue_watcher.py` daemon, open a new terminal
and run:

    cd /home/wrlbuild/wr-buildscripts
    ./devbuild_queue_watcher.py \
      --config test/test_scheduler_config.yaml

To start the `devbuild_watcher.py` daemon, open a new terminal
and run:

    cd /home/wrlbuild/wr-buildscripts
    ./devbuild_watcher.py --redis localhost --debug

To see the builds in progress go to: http://localhost:5000

Now open another terminal and go a wrlinux dev tree (not the one in
/home/wrlbuild), make sure it is up to date and add some commits
anywhere in the tree. Then run

    wrgit devbuild --redis <external ip of test machine> \
        --config test/single.yaml

Feel free to use or add a configuration that better suits your
testing.

Any changes to the webapp are automatically detected and the app is
reloaded when the `--debug` flag is used. The other applications
require a manual restart, but I may setup a Guardfile to have guard
auto reload the applications.

## Wraxl scheduler development

The scheduler is written in Python and python development and
management of dependencies can be a pain. Scheduler development uses
virtualenv and setuptools to isolate the development area and required
packages from the host system.

### Python Dev Environment prerequisites

I prefer not to use the system pip and virtualenv.

    sudo aptitude purge python-pip python-virtualenv

Because the scheduler uses yaml and the standard python yaml parser
has code that must be compiled, some packages must be installed first:

    make system

### Python setup

The virtualenv setup has been added to the Makefile:

    make setup

The setup step creates a virtualenv in "develop" mode where the python
path contains a link to the python files in the wraxl directory
allowing them to be edited without requiring any build steps.

The scheduler requires other infrastructure like a mesos master and
agent.

    ./start_test_wraxl_cluster.sh --registry wr-docker-registry

This will start the mesos master and redis database locally. To start
the scheduler using the virtualenv.

    source ~/.local/bin/virtualenvwrapper.sh
    workon wraxl_env
    wraxl_scheduler --master zk://localhost:2181/mesos \
      --redis localhost --config $PWD/test/test_scheduler_config.yaml
