#!/usr/bin/env python
"""Create a package for the wraxl scheduler so it can packaged using pex"""
from setuptools import setup

setup(
    name="wraxl",
    version="0.1",
    packages=['wraxl'],
    package_data={
        '': ['*.yaml'],
    },
    install_requires=[
        'pyramid==1.6.1',
        'mesos.native==0.27.2',
        'mesos.interface==0.27.2',
        'rq>=0.5.6',
        'redis>=2.10.0',
        'hiredis>=0.2.0',
        'protobuf==2.6.1',
        'requests>=2.9.0',
        'decorator>=4.0.0',
        'PyYAML',
    ],
    entry_points={
        'console_scripts': [
            'wraxl_scheduler = wraxl.scheduler:main',
        ],
    },
    test_suite='nose.collector',
    tests_require=['nose', 'docker-py'],
    author="Konrad Scherer",
    author_email="kmscherer@gmail.com",
    description="A Mesos framework for Wind River Linux builds and tests",
    license="Apache2",
    url='http://kscherer.github.io',
)
