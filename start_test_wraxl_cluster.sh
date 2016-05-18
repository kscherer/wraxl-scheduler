#!/bin/bash
set -uo pipefail
IFS=$'\n\t'

# taken from http://stackoverflow.com/questions/4023830/bash-how-compare-two-strings-in-version-format
function vercomp {
    if [[ "$1" == "$2" ]]; then
        return 0
    fi
    local IFS=.
    local i ver1=($1) ver2=($2)
    # fill empty fields in ver1 with zeros
    for ((i=${#ver1[@]}; i<${#ver2[@]}; i++))
    do
        ver1[i]=0
    done
    for ((i=0; i<${#ver1[@]}; i++))
    do
        if [[ -z ${ver2[i]} ]]
        then
            # fill empty fields in ver2 with zeros
            ver2[i]=0
        fi
        if ((10#${ver1[i]} > 10#${ver2[i]}))
        then
            return 1
        fi
        if ((10#${ver1[i]} < 10#${ver2[i]}))
        then
            return 2
        fi
    done
    return 0
}

usage() {
    cat <<EOF
Usage $0 [--registry] [--file] [--rm] [--with-lava]
  --registry=(ala|yow|pek)-lpdfs01: Docker registry to download images from.
     Will attempt to locate closest registry if not provided.

  --file <compose yaml>: Extra compose yaml file(s) to extend wraxl_test.yml
     Accepts multiple --file parameters

  --rm: Delete containers and volumes when script exits

  --with-lava: Creates lava server and data volumes to integrate with wraxl scheduler
EOF
    exit 1
}

CLEANUP=0
WITH_LAVA=0
DEV_MODE=0
export LOG_LEVEL=WARNING
export LAVA_VERSION=2016.4
export REGISTRY=
declare -a FILES=

while [ "$#" -gt 0 ]; do
    case "$1" in
        --registry=*)     REGISTRY="${1#*=}"; shift 1;;
        --registry)       REGISTRY="$2"; shift 2;;
        --file)           FILES=("${FILES[@]}" --file $2); shift 2;;
        --log=*)          LOG_LEVEL="${1#*=}"; shift 1;;
        --rm)             CLEANUP=1; shift 1;;
        --with-lava)      WITH_LAVA=1; shift 1;;
        --dev)            DEV_MODE=1; shift 1;;
        --lava-version=*) LAVA_VERSION="${1#*=}"; shift 1;;
        *)            usage ;;
    esac
done

command -v docker >/dev/null 2>&1 || { echo >&2 "I require docker but it's not installed. https://docs.docker.com/install/  Aborting."; exit 1; }

# require docker version >= 1.9.1
DOCKER_VERSION=$(docker --version | cut -d' ' -f 3)
vercomp '1.9.1' "$DOCKER_VERSION"
if [ $? != '2' ]; then
    echo >&2 "Require docker version 1.9.1 or later. Aborting"
    exit 1
fi

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

# require docker-compose version >= 1.7.0
DCOMPOSE_VERSION=$(docker-compose --version | cut -d' ' -f 3)
vercomp '1.6.2' "$DCOMPOSE_VERSION"
if [ $? != '2' ]; then
    echo >&2 "Require docker-compose version 1.7.0 or later. Aborting"
    exit 1
fi

echo "Docker Compose is present and is version $DCOMPOSE_VERSION"

if [ -z "$REGISTRY" ]; then
    echo "The closest internal docker registry was not specified with --registry"
    echo "The script will attempt to detect the closest docker registry"
    echo "Retrieving external ip address to determine location. May take a minute."
    external_ip=$(dig +short @resolver1.opendns.com myip.opendns.com)

    #only look at first 2 parts of ip address
    classB_subnet=$(echo "${external_ip}" | cut -d. -f1-2)
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

get_primary_ip_address() {
    # show which device internet connection would use and extract ip of that device
    ip=$(ip -4 route get 8.8.8.8 | awk 'NR==1 {print $NF}')
    echo "$ip"
}

export HOST="$HOSTNAME"

# require a $HOSTNAME with a proper DNS entry
host "$HOSTNAME" > /dev/null 2>&1
if [ $? != 0 ]; then
    echo "The hostname for this system is not in DNS. Attempting ip address fallback"
    export HOST=$(get_primary_ip_address)
fi

export HOSTIP=$(get_primary_ip_address)

if [ "$WITH_LAVA" == '1' ]; then
    LAVA_IMAGE="${REGISTRY}:5000/lava:${LAVA_VERSION}"
    LAVA_IMAGE_ID=$(${DOCKER_CMD[*]} images "$LAVA_IMAGE" )
    if [ -z "$LAVA_IMAGE_ID" ]; then
        echo "Pulling $LAVA_IMAGE"
        ${DOCKER_CMD[*]} pull "$LAVA_IMAGE"
    fi
    LAVA_WORKER_IMAGE="${REGISTRY}:5000/lava-worker:${LAVA_VERSION}"
    LAVA_WORKER_IMAGE_ID=$(${DOCKER_CMD[*]} images "$LAVA_WORKER_IMAGE" )
    if [ -z "$LAVA_WORKER_IMAGE_ID" ]; then
        echo "Pulling $LAVA_WORKER_IMAGE"
        ${DOCKER_CMD[*]} pull "$LAVA_WORKER_IMAGE"
    fi
    echo "$LAVA_IMAGE and $LAVA_WORKER_IMAGE are installed"

    # check if lava-server-data already exists
    ${DOCKER_CMD[*]} inspect lava-server-data &> /dev/null
    if [ $? != 0 ]; then
        echo "Creating lava-server-data data-only container"
        ${DOCKER_CMD[*]} create -v /var/lib/postgresql -v /var/log -v /run \
                         -v /var/lib/lava-server --name lava-server-data \
                         "${LAVA_IMAGE}" /bin/true
        echo "Initial database setup"
        mkdir -p /tmp/lava-server
        curl -s -o /tmp/lava-server/lava_backup.db.gz \
             http://ala-git/cgit/lpd-ops/lava-wraxl.git/plain/lava/lava_backup.db.gz
        ${DOCKER_CMD[*]} run -it --rm --volumes-from lava-server-data \
                         -v /tmp/lava-server:/tmp --name lava-server-init \
                         -h lava-server "${LAVA_IMAGE}" \
                         /bin/lava_db_restore.sh &> /dev/null
        echo "Initial database restored. Use 'docker exec -it wraxlscheduler_lava-server_1 lava-server manage create_wraxl_worker --hostname $HOSTNAME' to create initial devices once lava server is started."
    else
        echo "lava-server-data container already exists."
    fi
    FILES=(--file wraxl_lava.yml "${FILES[@]}")
    echo "Lava UI will be available at https://$HOSTIP"
fi

if [ "$DEV_MODE" == '0' ]; then
    FILES=(--file wraxl_test.yml --file wraxl_local_sched.yml "${FILES[@]}")
else
    FILES=(--file wraxl_test.yml "${FILES[@]}")
fi

echo "Mesos Master UI will be available at http://$HOSTIP:5050"
echo Starting wraxl with: docker-compose ${FILES[*]} up

sleep 1

docker-compose ${FILES[*]} up --abort-on-container-exit

if [ "$CLEANUP" == '1' ]; then
    echo "Cleaning up images and volumes"
    docker-compose ${FILES[*]} rm --force -v --all
fi
