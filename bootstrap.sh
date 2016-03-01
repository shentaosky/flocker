#!/bin/sh

# certifications names 
FLOCKER_DIR=/etc/flocker
CLUSTER_CRT=cluster.crt
#CLUSTER_KEY=cluster.key
PLUGIN_CRT=plugin.crt
NODE_CRT=node.crt
CONTROLL_CRT=control-service.crt
USER_CRT=user.crt

# agent.yml file might be different between nodes
AGENT_YML=agent.yml

HOSTNAME=`hostname`

DEBUG=${DEBUG:-"0"}

FLOCKER_OPTS=${FLOCKER_OPTS:-""}

usage()
{
cat << EOF
usage: 
  $(basename $0) [ROLE] 
  ROLE: flocker-control| flocker-dataset-agent| flocker-container-agent| flocker-docker-plugin
  Environment : 
    \$SKYDNS_PATH : skydns path on etcd server 
    \$DEBUG : wait for debug when docker exiting 
EOF
}

set -x
pushd $FLOCKER_DIR
#set -e

# require /etc/flocker/cluster.{crt,key} to generate keys
for file in $CLUSTER_CRT
do
  [ -f $file ] || {
    echo "${FLOCKER_DIR}/${file} is missing, exit now"
    exit 1
  }
done

case $1 in
  flocker-control)
    [ -f $CONTROLL_CRT ] || {
      echo "$CONTROLL_CRT is missing"
    }
    chmod 600 control-service.*
  ;;
  flocker-dataset-agent)
    [ -f $AGENT_YML ] || {
      echo "$AGENT_YML is missing"
    }
    [ -f $NODE_CRT ] || {
      echo "$NODE_CRT is missing"
    }
    chmod 600 node.*
  ;;
  flocker-container-agent)
    [ -f $NODE_CRT ] || {
      echo "$NODE_CRT is missing"
    }
  ;;
  flocker-docker-plugin)
    [ -f $NODE_CRT ] || {
      echo "$NODE_CRT is missing"
    }
    # all docker-plugin share the same crt
    [ -f $PLUGIN_CRT ] || {
      echo "$PLUGIN_CRT is missing"
    }
    # clean up existing plugin file
    [ -e /run/docker/plugins/flocker/flocker.sock ] && rm -f /run/docker/plugins/flocker/flocker.sock*
    [ -e /var/run/docker/plugins/flocker/flocker.sock ] && rm -f /var/run/docker/plugins/flocker/flocker.sock*
  ;;
  *)
    usage
    exit 1
  ;; 
esac

# run the command
$1 $FLOCKER_OPTS

set +x

[ $DEBUG -eq 1 ] && {
  echo "Waiting for debug before exit"
  while true 
  do
    sleep 10
  done
}
