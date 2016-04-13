# Wraxl Scheduler

The code for the scheduler is located in wraxl/*.py files. It uses
standard python development tooling and pex to create a standalone
bundle dist/wraxl_scheduler.

By default, it reads config from /etc/wraxl_scheduler.yaml. This
config file contains the current releases 5,6,7,8 and master. Each
release has a weight, name, file with configurations and a list of
valid host containers for that release.

When an offer is received, the scheduler chooses a release using a
weighted random selection algorithm, then chooses host and
configuration as random.

## Jobs scheduled using Python RQ

Using existing Python RQ installation on lpd-web, I created three
queues: wraxl_high, wraxl_low and wraxl_world. The build scheduler
monitors these queues. If a high priority resource is offered and
there is a task on any of these queues, the task is moved to temporary
queue and a container running the RQ worker is started. The worker
will then execute the task in burst mode and exit when the task
execution is complete.

This setup is used to rebuild native sstate daily. It is used by OVP
project to build daily images for LAVA tests. It is also used by
wrgit devbuild.

## Wraxl development

Use `make` to build the pex bundle. Use `make image` to build the
mesos-scheduler docker image.

Use the `start_wraxl_test_cluster.sh` to start the full wraxl stack on
a single machine using docker-compose. For more info please read
local_test_setup.md.

