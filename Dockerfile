FROM 172.16.1.41:5000/centos:7.2.1511

MAINTAINER HuaichengZheng "huaicheng.zheng@transwarp.io"

WORKDIR /root

# install epel
RUN echo "export TERM=xterm" >> /root/.bashrc && \
    echo -e "[transwarp]\nname = Transwarp\nbaseurl = ftp://172.16.1.32/pub/centos7-1511-everything\ngpgcheck = 0" > /etc/yum.repos.d/Transwarp.repo && \
    echo -e "[transwarp-zfs]\nname = Transwarp-zfs\nbaseurl = ftp://172.16.1.32/pub/centos7-1511-zfs\ngpgcheck = 0" > /etc/yum.repos.d/Transwarp-zfs.repo && \
    yum --disablerepo='*' --enablerepo=transwarp,transwarp-zfs -y --nogpgcheck install epel-release openssh-server redhat-lsb \
           kernel-devel gcc zfs wget git python-devel libffi-devel openssl-devel iptables python-pip && \
    yum clean all

# install flocker
RUN mkdir /worksapce
WORKDIR /worksapce
#RUN wget https://bootstrap.pypa.io/get-pip.py && \
#         python get-pip.py

# install require package in seperate step to accelerate rebuild
ADD requirements.txt /root/flocker/

RUN pip install --trusted-host 172.16.1.41 -i http://172.16.1.41:4321/root/dev/+simple/ -r /root/flocker/requirements.txt

ADD ./ /root/flocker
RUN pushd /root/flocker && python setup.py install --root /; popd ; rm -rf /root/flocker


# copy bootstrap.sh
WORKDIR /
COPY bootstrap.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/bootstrap.sh
