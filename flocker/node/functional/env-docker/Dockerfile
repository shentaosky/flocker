# Docker image that writes out the provided environment to /data/env and then
# exits. Existence of the file /data/env indicates the file is fully written.
FROM 172.16.1.41:5000/busybox
CMD ["/bin/sh",  "-c", "env > /data/temp && mv /data/temp /data/env"]
