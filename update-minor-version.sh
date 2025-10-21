#!/bin/bash
#
#
podman-compose down|| sleep 3
sudo chown jimmer deploy/httpd/certs/*.*
tar -czf "../turbohedi-0.${1}-$(date +%y%m%d)-01.tgz" .
if [[ "$?" -ne "0" ]]; then
	echo "tar command failed YO!"
fi

