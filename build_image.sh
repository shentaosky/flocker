#!/bin/bash

set -e

BIN=`dirname "${BASH_SOURCE-$0}"`
BIN=`cd "$BIN">/dev/null; pwd`

usage() {
  echo "
usage: $0 <options>
require:
     -t IMAGE_TAG                 docker image tag
     -r DOCKER_REGISTRY           prefix of docker image name, so images can be push to remote registry
optional:
  "
  exit 1
}

until [ $# -eq 0 ]
do
  case $1 in
    -t )
      IMAGE_TAG=$2
      shift 2
    ;;
    -r )
      DOCKER_REGISTRY=$2
      shift 2
    ;;
    * )
      usage
    ;; 
  esac
done


COMPONENT=transwarp-flocker
DOCKER_REGISTRY=${DOCKER_REGISTRY:-"172.16.1.41:5000"}
[ -z "$IMAGE_TAG" ] && {
  IMAGE_TAG=$DOCKER_REGISTRY/`whoami`/$COMPONENT:`date +"%Y%m%d-%H%M%S"`
  echo "no docker images name given, use $IMAGE_TAG"
}

#=======================================================================================================
sudo docker build --pull=true -t $IMAGE_TAG . || {
  echo "Build Docker Images $IMAGE_TAG failed"
  exit 1
}

set +x
echo -e "\nBuild $IMAGE_TAG success\n"
