#!/usr/bin/env bash
# assumes running in DOT-Commercial dir

mkdir -p configuration
mkdir -p data

FILE="data/carriers/FMCSA_CENSUS1_2023Feb.zip"
if [ ! -f "$FILE" ]; then
    mkdir -p data/carriers
    curl https://ai.fmcsa.dot.gov/SMS/files/FMCSA_CENSUS1_2023Feb.zip --output "$FILE"
    unzip "$FILE" -d data/carriers
fi

FILE=data/inspections/Inspection_2023Feb.zip
if [ ! -f "$FILE" ]; then
    mkdir -p data/inspections
    curl ftp://ftp.senture.com/Inspection_2023Feb.zip --output "$FILE"
    unzip "$FILE" -d data/inspections
fi

FILE=data/crashes/Crash_2023Feb.zip
if [ ! -f "$FILE" ]; then
    mkdir -p data/crashes
    curl ftp://ftp.senture.com/Crash_2023Feb.zip --output "$FILE"
    unzip "$FILE" -d data/crashes
fi