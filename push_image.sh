#!/bin/bash -x

IMAGE="$1"
WR=wr-docker-registry:5000
YOW=yow-lpdfs01:5000
ALA=ala-lpdfs01:5000
PEK=pek-lpdfs01:5000

for LOC in $YOW $ALA $PEK; do
    docker tag "$WR/$IMAGE" "$LOC/$IMAGE"

    docker push "$LOC/$IMAGE"

    docker rmi "$LOC/$IMAGE"
done
