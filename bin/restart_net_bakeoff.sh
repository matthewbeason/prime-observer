#!/bin/zsh
launchctl kickstart -k gui/$(id -u)/com.mbeason.net-bakeoff.collector
launchctl kickstart -k gui/$(id -u)/com.mbeason.net-bakeoff.transform
launchctl kickstart -k gui/$(id -u)/com.mbeason.net-bakeoff.http
sleep 2
launchctl list | grep -i "com.mbeason.net-bakeoff"
ls -l data/bakeoff_$(date +%Y%m%d).csv viz/latest.csv
