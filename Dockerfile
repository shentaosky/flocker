FROM        centos:7

MAINTAINER  MENG YANG "yang.meng@transwarp.io"

WORKDIR     /root

# install epel
RUN         yum repolist && yum update -y

# instatll base package
RUN         yum install -y epel-release

# install tools
RUN         yum install -y openssh-server redhat-lsb

# install zfs
RUN         yum localinstall -y --nogpgcheck https://download.fedoraproject.org/pub/epel/7/x86_64/e/epel-release-7-5.noarch.rpm && \
            yum localinstall -y --nogpgcheck http://archive.zfsonlinux.org/epel/zfs-release.el7.noarch.rpm && \
            yum install -y kernel-devel zfs

# install flocker
ENV         FLOCKER_VERSION 1.9.0-1
RUN         yum install -y wget git python-devel libffi-devel openssl-devel iptables
RUN         mkdir /worksapce
WORKDIR     /worksapce
RUN         wget https://bootstrap.pypa.io/get-pip.py && \
            python get-pip.py

# install require package in seperate step to accelerate rebuild
ADD         flocker/requirements.txt /root/flocker/
RUN         pip install -r /root/flocker/requirements.txt 

ADD         flocker /root/flocker
RUN         pushd /root/flocker && python setup.py install --root /; popd

# copy bootstrap.sh
WORKDIR     /
COPY        bootstrap.sh /usr/local/bin/
RUN         chmod +x /usr/local/bin/bootstrap.sh
