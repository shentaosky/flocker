FROM        centos:7

MAINTAINER  HuaichengZheng "huaicheng.zheng@transwarp.io"

WORKDIR     /root

# install epel
RUN         echo "export TERM=xterm" >> /root/.bashrc && \
            yum repolist && yum update -y && yum install -y epel-release openssh-server redhat-lsb && \
            yum localinstall -y --nogpgcheck https://download.fedoraproject.org/pub/epel/7/x86_64/e/epel-release-7-5.noarch.rpm && \
            yum localinstall -y --nogpgcheck http://archive.zfsonlinux.org/epel/zfs-release.el7.noarch.rpm && \
            yum install -y kernel-devel gcc zfs wget git python-devel libffi-devel openssl-devel iptables && yum clean all

# install flocker
RUN         mkdir /worksapce
WORKDIR     /worksapce
RUN         wget https://bootstrap.pypa.io/get-pip.py && \
            python get-pip.py

# install require package in seperate step to accelerate rebuild
ADD         requirements.txt /root/flocker/

RUN         pip install -r /root/flocker/requirements.txt

ADD         ./ /root/flocker
RUN         pushd /root/flocker && python setup.py install --root /; popd ; rm -rf /root/flocker


# copy bootstrap.sh
WORKDIR     /
COPY        bootstrap.sh /usr/local/bin/
RUN         chmod +x /usr/local/bin/bootstrap.sh
