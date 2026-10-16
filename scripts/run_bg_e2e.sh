#!/bin/bash
cd /home/z/my-project/treehole-app
setsid nohup python3 scripts/test_e2e.py > /tmp/test_e2e2.log 2>&1 < /dev/null &
exit 0
