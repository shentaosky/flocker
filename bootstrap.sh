#!/bin/sh

# certifications names 
FLOCKER_DIR=/etc/flocker
CLUSTER_CRT=cluster.crt
PLUGIN_CRT=plugin.crt
NODE_CRT=node.crt
CONTROLL_CRT=control-service.crt
USER_CRT=user.crt

# agent.yml file might be different between nodes
AGENT_YML=agent.yml

HOSTNAME=`hostname`

FLOCKER_OPTS=${FLOCKER_OPTS:-""}

usage()
{
cat << EOF
usage: 
  $(basename $0) [ROLE] 
  ROLE: master| regionserver 
  Environment : 
    \$SKYDNS_PATH : skydns path on etcd server 
    \$DEBUG : wait for debug when docker exiting 
EOF
}

set -x
pushd $FLOCKER_DIR
#set -e

# require /etc/flocker/cluster.crt to generate keys
for file in $CLUSTER_CRT
do
  [ -f $file ] || {
    echo "${FLOCKER_DIR}/${file} is missing, exit now"
    exit 1
  }
done

case $1 in
  flocker-control)
    # create control crt
    [ -f $CONTROLL_CRT ] || {
      flocker-ca create-control-certificate $HOSTNAME
      mv control-${HOSTNAME}.crt control-service.crt 
      mv control-${HOSTNAME}.key control-service.key
    }
    chmod 600 control-service.*
  ;;
  flocker-dataset-agent)
    # create node crt if not exist 
    [ -f $NODE_CRT ] || {
      node_crt=`flocker-ca create-node-certificate |cut -d " " -f 2|cut -d "." -f 1`
      mv ${node_crt}.crt node.crt
      mv ${node_crt}.key node.key
    }
    chmod 600 node.*
  ;;
  flocker-container-agent)
    for i in `seq 1 100`
    do
      [ -f $NODE_CRT ] && break
      echo "waiting $NODE_CRT to become available"
      sleep 5
    done
  ;;
  flocker-docker-plugin)
    for i in `seq 1 100`
    do
      [ -f $NODE_CRT ] && break
      echo "waiting $NODE_CRT to become available"
      sleep 5
    done
    # all docker-plugin share the same crt
    [ -f $PLUGIN_CRT ] || {
      echo "$PLUGIN_CRT missing, exit now"
      exit 1
    }
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
