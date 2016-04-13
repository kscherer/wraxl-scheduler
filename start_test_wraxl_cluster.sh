#!/bin/bash
set -uo pipefail
IFS=$'\n\t'

command -v docker >/dev/null 2>&1 || { echo >&2 "I require docker but it's not installed. https://docs.docker.com/install/  Aborting."; exit 1; }

DOCKER_CMD="docker"
if groups | grep -vq docker; then
    echo "This user is not in the docker group. Will attempt to run docker info using sudo."
    DOCKER_CMD=(sudo docker)
fi

${DOCKER_CMD[*]} info > /dev/null 2>&1
if [ $? != 0 ]; then
    echo >&2 "Unable to run '${DOCKER_CMD[*]}'. Either give the user sudo access to run docker or add it to the docker group. Aborting."
    exit 1
fi

echo 'Successfully ran docker info'

command -v docker-compose >/dev/null 2>&1 || { echo >&2 "I require docker-compose but it's not installed. https://docs.docker.com/compose/install/  Aborting."; exit 1; }

#make sure current dir is same as location of script
cd "$(dirname "$0")"
source common.sh

# require docker-compose version >= 1.5.0
DCOMPOSE_VERSION=$(docker-compose --version | cut -d' ' -f 3)
vercomp '1.4.2' "$DCOMPOSE_VERSION"
if [ $? != '2' ]; then
    echo >&2 "Require docker-compose version 1.5.0 or later. Aborting"
    exit 1
fi

echo "Docker Compose is present and is version $DCOMPOSE_VERSION"

export REGISTRY=
FILES=(--file wraxl_test.yml)

while [ "$#" -gt 0 ]; do
    case "$1" in
        --registry=*) REGISTRY="${1#*=}"; shift 1 ;;
        --registry)   REGISTRY="$2"; shift 2;;
        --file)       FILES=("${FILES[@]}" --file $2);shift 2;;
    esac
done

for i in "$@"
do
    case $i in
    esac
    shift
done

if [ -z "$REGISTRY" ]; then
    echo "The closest internal docker registry was not specified with --registry"
    echo "The script will attempt to detect the closest docker registry"
    echo "Retrieving external ip address to determine location. May take a minute."
    external_ip=$(dig +short @resolver1.opendns.com myip.opendns.com)

    #only look at first 2 parts of ip address
    classB_subnet=$(echo ${external_ip} | cut -d. -f1-2)
    if [ "x$classB_subnet" == "x128.224" ]; then
        REGISTRY=yow-lpdfs01
    elif [ "x$classB_subnet" == "x147.11" ]; then
        REGISTRY=ala-lpdfs01
    elif [ "x$classB_subnet" == "x106.120" ]; then
        REGISTRY=pek-lpdfs01
    else
        echo "Unable to determine closest registry. You will need to start the script with --registry"
        echo "and choose one of the three available registries: ala-lpdfs01, yow-lpdfs01 and pek-lpdfs01"
        exit 1
    fi
    echo "Using registry $REGISTRY. Next time call script with --registry=$REGISTRY"
else
    echo "Using registry $REGISTRY."
fi

export HOST="$HOSTNAME"
export HOSTIP=$(hostname --ip-address)

# require a $HOSTNAME with a proper DNS entry
host "$HOSTNAME" > /dev/null 2>&1
if [ $? != 0 ]; then
    echo "The hostname for this system is not in DNS. Attempting ip address fallback"
    export HOST=$(hostname --ip-address)
fi

if [ -d '/tmp/mesos/slaves' ]; then
    echo >&2 "The /tmp/mesos directory must be empty. Aborting"
    exit 1
fi

echo "Mesos Master UI will be available at http://$HOSTIP:5050"
echo Starting wraxl with: docker-compose ${FILES[*]} up

sleep 1

docker-compose ${FILES[*]} up
