SHELL = /bin/bash #requires bash
VERSION = 0.25.0
VENV = $(HOME)/.virtualenvs/wraxl_env
PEX = $(VENV)/bin/pex
TMP_EGG_DIR = .tmp/mesos.native
EGG = $(TMP_EGG_DIR)/mesos.native-$(VERSION)-py2.7-linux-x86_64.egg
DEPS = setup.py $(wildcard wraxl/*.py) wraxl/scheduler.yaml
PIP = $(HOME)/.local/bin/pip
VENVWRAPPER = $(HOME)/.local/bin/virtualenvwrapper.sh
DEBS = python-dev libyaml-dev

.PHONY: build image setup clean test help

.DEFAULT_GOAL := build

help:
	@echo "Make options for wraxl scheduler development"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-10s\033[0m %s\n", $$1, $$2}'

$(EGG):
	mkdir -p $(TMP_EGG_DIR); \
	curl -s -o $(EGG) http://yow-mirror.wrs.com/mirror/mesos/mesos-$(VERSION)-py2.7-linux-x86_64.egg

$(PEX): $(VENV)

dist/wraxl_scheduler: $(DEPS) $(EGG) $(PEX)
	. $(VENV)/bin/activate; \
	rm -f ~/.pex/build/wraxl*; \
	python setup.py bdist_pex --bdist-all --pex-args="-v --repo=$(TMP_EGG_DIR)"

build: dist/wraxl_scheduler ## Default: Build a pex bundle of the wraxl scheduler

image: dist/wraxl_scheduler ## Create a docker image with latest wraxl scheduler pex bundle
	docker build --rm -t wr-docker-registry:5000/mesos-scheduler:$(VERSION) --file Dockerfile-wraxl-scheduler .

# mesos.interface must be installed as an egg to avoid hiding
# installation of mesos.native egg
$(VENV): $(EGG) $(VENVWRAPPER) .check
	export VIRTUALENVWRAPPER_VIRTUALENV=$(HOME)/.local/bin/virtualenv; \
	source $(VENVWRAPPER); \
	test -d $(VENV) || mkvirtualenv wraxl_env; \
	touch $(VENV); \
	. $(VENV)/bin/activate; \
	pip install --egg mesos.interface==$(VERSION); \
	easy_install $(EGG); \
	pip install pylint docker-py nose flake8 pex; \
	touch $(PEX); \
	python setup.py develop;

setup: $(PEX) ## Install all python dependencies in wraxl_env virtualenv.

system: ## Convenience target for installing system libraries on Ubuntu/Debian
	sudo apt-get install $(DEBS)

.check: ## Verify system libraries are installed
	@echo "Verifying system library installation"
	@if [ -e /usr/bin/dpkg ]; then \
		for deb in $(DEBS); do \
			dpkg -L $$deb > /dev/null 2>&1; \
			if [ "$$?" != "0" ]; then \
				echo "Package $$deb must be installed. Run 'make system'."; \
				exit 1; \
			fi; \
		done; \
	else \
		echo "WARNING: unable to verify system libraries installed"; \
	fi; \
	touch .check

$(PIP):
	wget -O /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py; \
	python /tmp/get-pip.py --user; \
	rm -f /tmp/get-pip.py

$(VENVWRAPPER): $(PIP)
	$(PIP) install --user --upgrade virtualenv virtualenvwrapper

clean: ## Delete virtualenv and all build directories
	rm -rf $(VENV) wraxl.egg-info build dist .check

test: ## Run tests
	. $(VENV)/bin/activate; python setup.py test

dev: ## Run scheduler locally without building pex file
ifndef MASTER
	$(error MASTER which defines the ip address of mesos master is required )
endif
ifndef HOST_IP
	@echo "Detecting primary IP. When behind a VPN this will not be correct"
	@echo "This is the IP used by the mesos master to connect to the scheduler"
	$(eval HOST_IP=$(shell ip -4 route get 8.8.8.8 | awk 'NR==1 {print $$NF}'))
endif
	. $(VENV)/bin/activate; LIBPROCESS_IP=$(HOST_IP) python -m wraxl.scheduler \
		--master $(MASTER):5050 --hostname $(shell hostname) --redis $(MASTER) \
        --config $$PWD/test/test_scheduler_config.yaml --config_dir ../wr-buildscripts/
