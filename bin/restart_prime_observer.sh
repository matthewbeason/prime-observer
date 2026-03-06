#!/bin/zsh
launchctl kickstart -k gui/$(id -u)/com.mbeason.prime-observer.collector
launchctl kickstart -k gui/$(id -u)/com.mbeason.prime-observer.transform
launchctl kickstart -k gui/$(id -u)/com.mbeason.prime-observer.http
sleep 2
launchctl list | grep -i "com.mbeason.prime-observer"
ls -l data/bakeoff_$(date +%Y%m%d).csv viz/latest.csv
