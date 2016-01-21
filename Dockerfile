FROM        centos:7

MAINTAINER  MENG YANG "yang.meng@transwarp.io"

WORKDIR /root
# install epel
RUN         yum repolist
RUN         yum install -y epel-release

# install tools
RUN         yum install -y openssh-server

# install zfs
RUN         yum localinstall -y --nogpgcheck https://download.fedoraproject.org/pub/epel/7/x86_64/e/epel-release-7-5.noarch.rpm && \
            yum localinstall -y --nogpgcheck http://archive.zfsonlinux.org/epel/zfs-release.el7.noarch.rpm && \
            yum install -y kernel-devel zfs

# install flocker
RUN         yum list installed clusterhq-release || yum install -y https://clusterhq-archive.s3.amazonaws.com/centos/clusterhq-release$(rpm -E %dist).noarch.rpm && \
            yum install -y clusterhq-flocker-node

# just for debug
RUN         yum install -y vim

# config
VOLUME      ["/etc/flocker"]
VOLUME      ["/var/lib/flocker"]
VOLUME      ["/flocker"]

# copy bootstrap.sh
COPY        bootstrap.sh /usr/local/bin/
RUN         chmod +x /usr/local/bin/bootstrap.sh
